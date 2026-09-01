"""Behavioural tests for handoff-doc RESOLUTION in scripts/resume-state.sh.

THE BUG THIS PINS (measured 2026-08-04, before the fix):
`resolve()` globbed only `claudedocs/handoff-*.md`. The civitai-manager repo
names its handoff `claudedocs/SESSION-HANDOFF.md`, so running the script there
printed

    handoff: (none found — git-only)
    DRIFT
      (none detected — live state matches the handoff's claims)

The DRIFT line is the real damage: it reconciled against NOTHING and reported a
reassuring result. A false green. The caller reads "no drift" as a fact about
the handoff when no handoff was ever loaded.

WHAT IS ASSERTED — the `handoff:` line names the EXACT file, never merely that
the script exited 0, because "exited 0" is true for the broken script too.

HERMETIC. Every fixture is a throwaway `git init` repo under tmp_path with no
`origin` remote and no `prod-kubeconfig`, which is what keeps the PR block, the
WORKLOAD block and the ALERTS block on their skip paths. `gh`, `kubectl` and
`curl` are additionally stubbed onto the front of $PATH as tripwires that log
and fail; `test_no_network_tool_is_ever_invoked` asserts the log stayed empty,
so a future change that reaches for the network fails here instead of hanging
on a real timeout. Measured runtime of the whole module: ~3.2 s cold, ~2.0-2.9 s
warm. (This line used to claim "well under a second", which was never measured.)

STYLE NOTE — the stub-fake-binaries-on-$PATH technique is
scripts/tests/test_release_wrapper.sh's convention. This suite is written as
pytest rather than bash on purpose: scripts/run-tests.sh (the flake gate) takes
`scripts/tests` as a pytest target, so it collects `test_*.py` ONLY. The two
`.sh` suites in this directory are run by hand and by nothing else. A guard the
gate cannot see is a guard that reports no failures.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

SCRIPT = REPO_ROOT / "scripts/resume-state.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git + bash on PATH",
)

# Docs that live beside a handoff in these repos and must NEVER resolve as one.
# The first six are real filenames lifted from civitai-manager's claudedocs/.
#
# The last three are ADVERSARIAL, added after a mutation SURVIVED the original
# set: broadening the fallback to `*[Hh][Aa][Nn][Dd]*.md` — a plausible "match
# handoff in any case" edit — passed all 26 cases, because not one decoy
# contained the letters HAND. A guard that only rules out the over-broad globs
# you happened to imagine is a guard calibrated to your own imagination. These
# three are HAND-but-not-HANDOFF, so any glob looser than the exact uppercase
# substring sweeps them in and fails here.
DECOY_DOCS = (
    "SOME-DESIGN.md",
    "COMFYUI-INTEGRATION-DESIGN.md",
    "SECURITY-AUDIT-v0.1.64.md",
    "LAUNCH-REDDIT-POST.md",
    "HUGGINGFACE-FALLBACK-RESEARCH.md",
    "BREADCRUMBS-AND-COPY-DESIGN.md",
    "HANDBOOK.md",
    "SHORTHAND-NOTES.md",
    "handling-errors.md",
    # 🔴 The tenth, added 2026-08-21 after a mutation SURVIVED at BOTH sites that
    # spell `*HANDOFF*.md` (the fallback chain and the prose scan): broadening
    # the uppercase glob to a case-INSENSITIVE variant. Not one of the nine
    # above contains the letters `handoff` in any case, so nothing could see it.
    # This one does, lowercase and not at the start, so it matches neither
    # `handoff-*.md` nor `*HANDOFF*.md` today and is swept in by any
    # case-folding of the second.
    "my-handoff-notes.md",
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def stub_bin(tmp_path_factory):
    """`gh`/`kubectl`/`curl` tripwires on the front of $PATH.

    They log their invocation and exit non-zero. Nothing in these fixtures
    should reach them; `test_no_network_tool_is_ever_invoked` proves it.
    """
    d = tmp_path_factory.mktemp("stubbin")
    log = d / "invocations.log"
    for name in ("gh", "kubectl", "curl"):
        # testlib.mockbin owns the shebang (/bin/sh, not #!/usr/bin/env). The
        # nix build sandbox that runs the authoritative gate has no
        # /usr/bin/env, and patchShebangs cannot reach a file a test writes at
        # runtime — so a hand-written shebang here is green on this host and red
        # only on the tier that gates merges. test_runtime_shebangs.py caught
        # exactly that in this file.
        # The gh stub answers with $STUB_GH_JSON when set, so a test can drive
        # the PR-reconciliation path both ways — answering and failing — without
        # a network. Unset (the default) it behaves as a pure tripwire: log the
        # invocation and fail, which is what `gh pr view` does offline.
        # $STUB_GH_OK_PRS (space-separated) additionally makes gh answer for
        # THOSE PR numbers only and fail for the rest. Without that selective
        # mode the fixtures could only make gh answer for all or none, which
        # left the PARTIAL-answer branch structurally unreachable — a mutation
        # of it survived the whole suite.
        body = f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\n'
        if name == "gh":
            body += (
                'if [ -n "${STUB_GH_JSON:-}" ]; then\n'
                '  if [ -n "${STUB_GH_OK_PRS:-}" ]; then\n'
                '    num=""\n'
                '    for a in "$@"; do\n'
                '      case "$a" in ""|*[!0-9]*) ;; *) num="$a"; break ;; esac\n'
                '    done\n'
                '    for p in $STUB_GH_OK_PRS; do\n'
                '      if [ "$num" = "$p" ]; then printf "%s\\n" "$STUB_GH_JSON"; exit 0; fi\n'
                '    done\n'
                '    exit 1\n'
                '  fi\n'
                '  printf "%s\\n" "$STUB_GH_JSON"; exit 0\n'
                'fi\n'
            )
        write_exec(d / name, body + "exit 1\n")
    return d, log


def make_repo(
    tmp_path,
    docs=(),
    name="fixture-repo",
    doc_body=None,
    files=(),
    branches=(),
    remote=None,
):
    """A throwaway git repo whose claudedocs/ holds exactly `docs`.

    By default: no `origin` remote (so SLUG stays empty and the PR block skips)
    and no prod-kubeconfig (so WORKLOAD/ALERTS skip). Docs are created in the
    order given and stamped with increasing mtimes, so `docs[-1]` is the NEWEST
    — which is what `ls -t | head -1` selects.

    `doc_body`   prose written into every doc (drives extract_prs/extract_branches)
    `files`      extra tracked paths, committed — so `git cat-file -e HEAD:<p>` hits
    `branches`   real branches created off the seed commit
    `remote`     an `origin` URL, which is what makes SLUG non-empty and sends
                 the script down the gh path
    """
    repo = tmp_path / name
    (repo / "claudedocs").mkdir(parents=True)
    env = _git_env(repo)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    tracked = ["README.md"]
    for rel in files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"contents of {rel}\n")
        tracked.append(rel)
    subprocess.run([*git, "add", *tracked], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "seed"], check=True, env=env)
    for br in branches:
        subprocess.run([*git, "branch", br], check=True, env=env)
    if remote:
        subprocess.run([*git, "remote", "add", "origin", remote], check=True, env=env)
    for i, doc in enumerate(docs):
        p = repo / "claudedocs" / doc
        p.write_text(doc_body if doc_body is not None else f"## {doc}\nsome handoff prose\n")
        # 1000s apart so `ls -t` ordering is unambiguous regardless of fs mtime
        # granularity; later entries in `docs` are newer.
        os.utime(p, (1_700_000_000 + i * 1000, 1_700_000_000 + i * 1000))
    return repo


def _git_env(repo):
    env = dict(os.environ)
    env.update(
        {
            # the host's real git config must not influence a fixture
            "GIT_CONFIG_GLOBAL": str(repo.parent / "gitconfig-global"),
            "GIT_CONFIG_SYSTEM": str(repo.parent / "gitconfig-system"),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            # 🔴 The SKILL block checks the DEPLOYED
            # ~/.claude/skills/<name>/SKILL.md against origin — a fact about the
            # HOST, not about these fixtures. Left on, every case here would
            # inherit this machine's deploy state (and a `!` gap in the nix
            # sandbox, which has no ~/.claude at all), turning the DRIFT
            # all-clear assertions into host-dependent noise.
            # EMPTY, not unset: `${RESUME_STATE_SKILL-resume}` reads unset as
            # "check /resume" and empty as "check none" — the latter is what this
            # suite means. scripts/tests/test_resume_state_skill_freshness.py
            # owns that block, including that the DEFAULT really is /resume.
            "RESUME_STATE_SKILL": "",
        }
    )
    return env


def run_resume(repo, stub_bin, *args, cwd=None, extra_env=None):
    """Run resume-state.sh with `repo` as cwd; return its stdout.

    🔴 `env=env` is LOAD-BEARING and was MISSING when this suite first shipped.
    Without it the subject inherits os.environ: the stub PATH never applied, so
    the script would reach the REAL gh/kubectl/curl, and STUB_LOG was unset so
    nothing could ever be logged. test_no_network_tool_is_ever_invoked was
    therefore asserting a STRUCTURAL zero — a counter wired to nothing, which is
    the exact failure its positive control was added to prevent. The control
    execs the stub DIRECTLY with env=env, a path the subject never took, so it
    validated the stub rather than the tripwire.

    Proven, not reasoned: injecting a `gh` call into resolve() left the tripwire
    GREEN before this fix and RED after (log shows one probe per run_resume).
    This also restores the GIT_CONFIG_GLOBAL/SYSTEM isolation to the script's
    own git calls, which likewise never reached them.
    """
    d, log = stub_bin
    env = _git_env(repo)
    env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    # The freshness check fetches origin. Most fixtures here carry either NO
    # remote or a fake `git@github.com:` URL, and a real fetch against those
    # means an SSH attempt to github.com — network, seconds, and a hole in this
    # module's hermeticity promise. Skipped by default; the freshness tests
    # below opt back IN, against a real bare repo on disk, so the fetch path is
    # exercised for real rather than mocked away.
    env["RESUME_STATE_SKIP_FETCH"] = "1"
    env.update(extra_env or {})
    out = subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(cwd or repo),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert out.returncode == 0, f"script failed rc={out.returncode}\n{out.stderr}"
    return out.stdout


def handoff_line(stdout):
    """The digest's `handoff:` line, stripped. Fails loudly if absent."""
    hits = [ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("handoff:")]
    assert len(hits) == 1, f"expected exactly one handoff: line, got {hits}\n{stdout}"
    return hits[0]


def branch_lines(stdout):
    """Every `branch <tok>: …` line the GIT/PR block emitted, stripped."""
    return [ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("branch ")]


def branch_tokens(stdout):
    """Just the token from each branch line — what the digest CLAIMS is a branch."""
    return [ln.split(":", 1)[0][len("branch "):] for ln in branch_lines(stdout)]


def drift_lines(stdout):
    """Everything after the `DRIFT` header, stripped."""
    lines = stdout.splitlines()
    i = lines.index("DRIFT")
    return [ln.strip() for ln in lines[i + 1 :] if ln.strip()]


def gap_lines(stdout):
    """Just the `! …` GAP entries, unprefixed. The `!!` banner is excluded."""
    return [ln[2:] for ln in drift_lines(stdout) if ln.startswith("! ")]


# --------------------------------------------------------------------------- #
# harness sanity — a fixture that cannot express the contest must fail LOUDLY
# --------------------------------------------------------------------------- #
def test_harness_can_observe_a_named_handoff(tmp_path, stub_bin):
    """POSITIVE CONTROL for `handoff_line`.

    Every other assertion here is "the line names file X" or "the line names
    nothing". A parser that never finds the line, or one wired to the wrong
    stream, would make the negative cases pass for free. This proves the
    instrument can read a real filename out of a real run.
    """
    repo = make_repo(tmp_path, docs=("handoff-positive-control.md",))
    assert handoff_line(run_resume(repo, stub_bin)) == (
        "handoff: handoff-positive-control.md"
    )


# --------------------------------------------------------------------------- #
# THE REGRESSION — an uppercase SESSION-HANDOFF.md must resolve
# --------------------------------------------------------------------------- #
def test_session_handoff_md_resolves(tmp_path, stub_bin):
    """civitai-manager's real shape: claudedocs/SESSION-HANDOFF.md only.

    RED before the fix — the digest said `handoff: (none found — git-only)`.
    """
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: SESSION-HANDOFF.md"


def test_bare_handoff_md_resolves(tmp_path, stub_bin):
    """The other member of the family the fallback is meant to reach."""
    repo = make_repo(tmp_path, docs=("HANDOFF.md",))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: HANDOFF.md"


def test_uppercase_family_beside_decoys_still_picks_the_handoff(tmp_path, stub_bin):
    """The decoys are NEWER than the handoff, so `ls -t` order cannot save us.

    If the fallback glob were broadened to something like `*.md` or `[A-Z]*.md`,
    this fails — a decoy would sort first.
    """
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md", *DECOY_DOCS))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: SESSION-HANDOFF.md"


# --------------------------------------------------------------------------- #
# NO REGRESSION — the existing lowercase glob keeps winning
# --------------------------------------------------------------------------- #
def test_lowercase_handoff_glob_still_resolves(tmp_path, stub_bin):
    repo = make_repo(tmp_path, docs=("handoff-foo.md",))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: handoff-foo.md"


def test_lowercase_wins_when_both_shapes_are_present(tmp_path, stub_bin):
    """PRECEDENCE. SESSION-HANDOFF.md is written LAST, so it is the NEWEST file.

    That ordering is deliberate: if the fallback were tried first, or if both
    globs were merged into one `ls -t`, the newest (SESSION-HANDOFF.md) would
    win and this fails. Today's behaviour must be preserved exactly —
    `handoff-*.md` wins regardless of age.
    """
    repo = make_repo(tmp_path, docs=("handoff-foo.md", "SESSION-HANDOFF.md"))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: handoff-foo.md"


def test_newest_lowercase_handoff_wins(tmp_path, stub_bin):
    """`ls -t | head -1` selection shape is unchanged: newest of the family."""
    repo = make_repo(tmp_path, docs=("handoff-alpha.md", "handoff-beta.md"))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: handoff-beta.md"


def test_newest_uppercase_handoff_wins(tmp_path, stub_bin):
    """Same selection shape inside the new fallback family."""
    repo = make_repo(tmp_path, docs=("HANDOFF.md", "SESSION-HANDOFF.md"))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: SESSION-HANDOFF.md"


# --------------------------------------------------------------------------- #
# NOT OVER-BROAD — design/audit/launch docs may never resolve as a handoff
# --------------------------------------------------------------------------- #
def test_decoy_docs_alone_resolve_nothing(tmp_path, stub_bin):
    repo = make_repo(tmp_path, docs=DECOY_DOCS)
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: (none found — git-only)"


@pytest.mark.parametrize("doc", DECOY_DOCS)
def test_each_decoy_doc_individually_resolves_nothing(tmp_path, stub_bin, doc):
    """Per-document, so a failure names the exact filename that leaked through."""
    repo = make_repo(tmp_path, docs=(doc,))
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: (none found — git-only)"


def test_empty_claudedocs_resolves_nothing(tmp_path, stub_bin):
    repo = make_repo(tmp_path, docs=())
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: (none found — git-only)"


def test_a_handoffish_name_that_is_not_a_md_file_is_ignored(tmp_path, stub_bin):
    """The fallback is scoped to `.md`, same as the existing glob."""
    repo = make_repo(tmp_path, docs=())
    (repo / "claudedocs" / "SESSION-HANDOFF.txt").write_text("not markdown\n")
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: (none found — git-only)"


# --------------------------------------------------------------------------- #
# the explicit-path form (already worked for any filename) must not regress
# --------------------------------------------------------------------------- #
def test_explicit_path_resolves_an_arbitrary_filename(tmp_path, stub_bin):
    repo = make_repo(tmp_path, docs=())
    doc = repo / "claudedocs" / "TOTALLY-CUSTOM-NAME.md"
    doc.write_text("## custom\n")
    assert handoff_line(run_resume(repo, stub_bin, str(doc))) == (
        "handoff: TOTALLY-CUSTOM-NAME.md"
    )


def test_explicit_path_beats_a_resolvable_glob(tmp_path, stub_bin):
    repo = make_repo(tmp_path, docs=("handoff-foo.md", "SESSION-HANDOFF.md"))
    doc = repo / "claudedocs" / "PICK-ME.md"
    doc.write_text("## pick me\n")
    assert handoff_line(run_resume(repo, stub_bin, str(doc))) == "handoff: PICK-ME.md"


# --------------------------------------------------------------------------- #
# the topic-slug form
# --------------------------------------------------------------------------- #
def test_slug_still_selects_within_the_lowercase_family(tmp_path, stub_bin):
    repo = make_repo(
        tmp_path, docs=("handoff-alpha-2026-01-01.md", "handoff-beta-2026-02-02.md")
    )
    assert handoff_line(run_resume(repo, stub_bin, "alpha")) == (
        "handoff: handoff-alpha-2026-01-01.md"
    )


def test_slug_degrades_into_the_uppercase_fallback(tmp_path, stub_bin):
    """DECIDED BEHAVIOUR: the slug gets NO fallback glob of its own.

    A slug that matches no `handoff-<slug>*.md` already falls through to the
    unqualified `handoff-*.md` glob today; the new fallback simply extends that
    same chain. In a repo whose only handoff is SESSION-HANDOFF.md there is
    nothing for a topic slug to disambiguate, so `resume-state.sh session`
    resolving it is the right answer and costs no extra code.
    """
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",))
    assert handoff_line(run_resume(repo, stub_bin, "session")) == (
        "handoff: SESSION-HANDOFF.md"
    )


def test_unmatched_slug_degrades_the_same_way_as_before(tmp_path, stub_bin):
    """Documented degradation, unchanged in shape: an unmatched slug falls back
    to "the newest handoff in this repo" rather than resolving nothing."""
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",))
    assert handoff_line(run_resume(repo, stub_bin, "no-such-topic")) == (
        "handoff: SESSION-HANDOFF.md"
    )


def test_slug_does_not_reach_the_decoys(tmp_path, stub_bin):
    """A slug matching a decoy's name must still resolve nothing — the slug is
    only ever interpolated into the `handoff-*` glob."""
    repo = make_repo(tmp_path, docs=("SOME-DESIGN.md",))
    assert handoff_line(run_resume(repo, stub_bin, "design")) == (
        "handoff: (none found — git-only)"
    )


def test_slug_is_anchored_to_the_handoff_prefix(tmp_path, stub_bin):
    """The slug must only ever fill `handoff-<slug>*.md`, never `*<slug>*.md`.

    This case exists because the test above was green for an INCIDENTAL reason:
    broadening line 84 to `*"$arg"*.md` survived the whole suite, purely because
    its decoy is SOME-DESIGN.md and its slug is `design` — a case mismatch.
    devrc really does carry claudedocs/browser-bridge-usage-audit-2026-08-02.md,
    so under that mutation `resume-state.sh browser` would resolve an audit
    document as a handoff. Lowercase decoy, lowercase slug, no coincidence left.
    """
    repo = make_repo(tmp_path, docs=("browser-bridge-usage-audit-2026-08-02.md",))
    assert handoff_line(run_resume(repo, stub_bin, "browser")) == (
        "handoff: (none found — git-only)"
    )


def test_resolution_is_scoped_to_claudedocs(tmp_path, stub_bin):
    """Both globs name `claudedocs/` explicitly; a handoff-shaped file anywhere
    else — repo root or a sibling directory — must not resolve. Untested until
    now: mutating the fallback to `*/*HANDOFF*.md` survived the whole suite."""
    repo = make_repo(tmp_path, docs=())
    (repo / "SESSION-HANDOFF.md").write_text("## root, not claudedocs\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "SESSION-HANDOFF.md").write_text("## sibling dir\n")
    assert handoff_line(run_resume(repo, stub_bin)) == "handoff: (none found — git-only)"


# --------------------------------------------------------------------------- #
# 🔴 #684 — AN ARGUMENT THAT DID NOT RESOLVE MUST NOT FALL BACK SILENTLY
#
# The chain ends in "newest claudedocs/handoff-*.md", and that last step could
# not tell "no argument supplied" (newest IS the contract) from "an argument was
# supplied and did not resolve" (newest is a guess). Both reached it, and the
# second was silent: the digest printed the fallback's filename on the `handoff:`
# line as though that were the intent, and every block below — the DRIFT
# all-clear included — was then correct about a document nobody asked for.
#
# `NEWEST` is written last in every fixture here, so it is the file `ls -t`
# selects. `WANTED` is deliberately NOT the newest: a test whose target is also
# the fallback target cannot tell resolution from luck.
# --------------------------------------------------------------------------- #
WANTED = "handoff-wanted-2026-01-01.md"
NEWEST = "handoff-newest-unrelated-2026-08-21.md"


def two_initiative_repo(tmp_path):
    return make_repo(tmp_path, docs=(WANTED, NEWEST))


def test_an_unresolvable_slug_is_reported_as_a_gap(tmp_path, stub_bin):
    """THE REGRESSION. RED before the fix: no `!` line at all.

    The fallback itself is preserved — the digest still reads the newest doc, so
    the GIT/PR block stays useful. What must not happen is doing that SILENTLY.
    """
    repo = two_initiative_repo(tmp_path)
    out = run_resume(repo, stub_bin, "definitely-not-a-real-topic-xyz")
    assert handoff_line(out) == f"handoff: {NEWEST}"          # fallback preserved
    gaps = gap_lines(out)
    assert gaps, f"an unresolvable argument fell back with no gap line\n{out}"
    joined = " ".join(gaps)
    assert "definitely-not-a-real-topic-xyz" in joined, gaps  # what was ASKED FOR
    assert NEWEST in joined, gaps                             # what was READ


def test_the_all_clear_is_withdrawn_when_the_argument_did_not_resolve(
    tmp_path, stub_bin
):
    """The actual damage was never the filename — it was the reassurance.

    RED before the fix: DRIFT printed "(none detected — live state matches the
    handoff's claims)" about an initiative the caller never named.
    """
    lines = drift_lines(run_resume(two_initiative_repo(tmp_path), stub_bin, "nope-xyz"))
    assert "matches the handoff's claims" not in " ".join(lines), lines


def test_a_no_argument_run_keeps_todays_behaviour_exactly(tmp_path, stub_bin):
    """CONTROL, not a regression test — this passes at base too, and says so.

    With no argument, newest IS the contract, so there is nothing to warn about.
    A fix that simply always warned would pass the two tests above and fail here.
    """
    out = run_resume(two_initiative_repo(tmp_path), stub_bin)
    assert handoff_line(out) == f"handoff: {NEWEST}"
    assert gap_lines(out) == [], out
    assert "matches the handoff's claims" in " ".join(drift_lines(out)), out


def test_a_slug_THAT_RESOLVES_is_not_reported_as_a_gap(tmp_path, stub_bin):
    """CONTROL for the other side: a working slug must stay silent."""
    out = run_resume(two_initiative_repo(tmp_path), stub_bin, "wanted")
    assert handoff_line(out) == f"handoff: {WANTED}"
    assert gap_lines(out) == [], out


def test_an_unresolvable_argument_with_nothing_to_fall_back_to_still_gaps(
    tmp_path, stub_bin
):
    """No handoff anywhere: the digest already said "(none found — git-only)",
    but never that an argument had been supplied and missed."""
    repo = make_repo(tmp_path, docs=())
    out = run_resume(repo, stub_bin, "no-such-topic")
    assert handoff_line(out) == "handoff: (none found — git-only)"
    assert any("no-such-topic" in g for g in gap_lines(out)), out


# --------------------------------------------------------------------------- #
# 🔴 #684 part 2 — A PATH QUOTED INSIDE A PROSE ARGUMENT IS A PATH
#
# /resume passes its topic argument through verbatim and its documented form
# carries the doc: "continue the X work; handoff: <path>". `[ -f "$arg" ]` is
# false for that whole sentence, the slug glob interpolated the sentence and
# matched nothing, and the incident followed. This is the part that would have
# PREVENTED it rather than merely reported it.
# --------------------------------------------------------------------------- #
def test_a_prose_argument_carrying_a_real_path_resolves_to_THAT_path(
    tmp_path, stub_bin
):
    """THE REGRESSION. RED before the fix: resolved NEWEST, the wrong initiative.

    This is the incident's literal shape — the requested doc is not the newest,
    which is the only reason the assertion can tell resolution from coincidence.
    """
    repo = two_initiative_repo(tmp_path)
    arg = f"continue the wanted work; handoff: {repo / 'claudedocs' / WANTED}"
    assert handoff_line(run_resume(repo, stub_bin, arg)) == f"handoff: {WANTED}"


def test_a_resolved_prose_path_is_a_resolution_not_a_warned_fallback(
    tmp_path, stub_bin
):
    """CONTROL, and an INVARIANT GUARD — declared, not counted as coverage.

    It answered the caller's question, so it must not also cry gap. Today that is
    structural: the gap is emitted inside the else-branch the prose path never
    enters, so no mutation of the gap condition can make this fail. It is here to
    pin the contract if that structure is ever flattened, not because it caught
    anything.
    """
    repo = two_initiative_repo(tmp_path)
    arg = f"pick up handoff: {repo / 'claudedocs' / WANTED} please"
    assert gap_lines(run_resume(repo, stub_bin, arg)) == []


@pytest.mark.parametrize(
    "wrap", ["{p}", "`{p}`", "({p})", "'{p}'", '"{p}"', "<{p}>", "{p},", "{p};"]
)
def test_one_layer_of_punctuation_around_the_path_is_stripped(
    tmp_path, stub_bin, wrap
):
    """Prose quotes its paths. Backticks and parens are how it does it."""
    repo = two_initiative_repo(tmp_path)
    p = wrap.format(p=repo / "claudedocs" / WANTED)
    assert handoff_line(run_resume(repo, stub_bin, f"resume {p} now")) == (
        f"handoff: {WANTED}"
    )


@pytest.mark.parametrize(
    "docs",
    [
        pytest.param((WANTED, NEWEST), id="two-candidates"),
        # 🔴 THE CASE THE TWO-CANDIDATE FIXTURE CANNOT SEE, and the audit of the
        # previous round found it exactly there: with one candidate the warning
        # was keyed on the COUNT alone, so an explicit-path miss produced
        # `handoff: SESSION-HANDOFF.md`, NO gap, and a clean DRIFT all-clear.
        # civitai-manager's real shape.
        pytest.param(("SESSION-HANDOFF.md",), id="one-candidate"),
    ],
)
def test_a_prose_path_that_does_NOT_exist_is_not_taken(tmp_path, stub_bin, docs):
    """ADVERSARIAL — pins the `-f` half, AND that the miss is always REPORTED.

    Dropping `-f` would make a renamed or foreign-checkout path resolve to
    something that is not there. But rejecting it silently is the worse half:
    the caller named a document, it is not on disk, and the tool substitutes a
    different one.

    🔴 UPDATED FOR #1164. This used to assert the substitution — `handoff:
    <the newest unrelated doc>` plus a gap line — because the fallback ran even
    for a NAMED path. The gap was honest and the digest under it was not: a
    complete, confident reconciliation of another initiative. A named-missing
    path now reconciles NOTHING, whatever the candidate count, and both fixtures
    hold docs the run could have fallen back to and must not.
    """
    repo = make_repo(tmp_path, docs=docs)
    arg = f"resume it; handoff: {repo / 'claudedocs' / 'handoff-gone-2026-01-01.md'}"
    out = run_resume(repo, stub_bin, arg)
    assert handoff_line(out) == "handoff: (none found — git-only)", out
    # 🔴 ASSERTED OVER THE WHOLE DIGEST, like the sibling in the part-2 block.
    # This read `doc not in handoff_line(out)` and COULD NOT FAIL: the line
    # above already pins `handoff_line(out)` to an exact literal that contains
    # none of `docs`, so the loop restated a settled fact while its comment
    # claimed something about the digest. Coverage that reads as coverage and
    # provides none is worse than none — it stops anyone looking. (audit of
    # #1197, F7.)
    for doc in docs:                       # nothing else was substituted either
        assert doc not in out, f"{doc} was reconciled anyway\n{out}"
    assert any("handoff-gone" in g for g in gap_lines(out)), out
    # …and the all-clear is withdrawn, which is the harm, not the filename.
    assert "matches the handoff's claims" not in " ".join(drift_lines(out)), out


def test_a_non_md_file_named_in_prose_is_not_taken_as_the_handoff(tmp_path, stub_bin):
    """ADVERSARIAL — pins the extension half of the shape test.

    Without it, any token that happens to name an existing file wins. The bait
    must EXIST and NOT end .md, and a name that is simply absent is rejected by
    the `-f` test instead, which would make this vacuous.

    🔴 THE BAIT MUST LIVE IN `claudedocs/`, or this test passes for a CHANGED
    REASON — the same vacuity class caught twice already in this file. When the
    bait was a bare `notes.txt` at the repo root it had no `/`, so `dir` equalled
    the token, the PARENT-DIRECTORY test rejected it first, and deleting the
    basename test left this GREEN. `claudedocs/notes.txt` clears the first test
    and can only be stopped by the second.

    🔴 This docstring used to say "`README.md` could not tell the two halves
    apart" and leave it there — and that set-aside was the defect the audit of
    #690 found: nothing covered `README.md`, and the first version of
    `embedded_md_path` resolved it. The README class is now covered directly, by
    `test_a_bare_md_token_in_prose_is_not_a_handoff_reference` and its four
    siblings below. Do not delete this note without reading them.
    """
    repo = make_repo(tmp_path, docs=(WANTED, NEWEST), files=("claudedocs/notes.txt",))
    assert handoff_line(
        run_resume(repo, stub_bin, "resume claudedocs/notes.txt please")
    ) == f"handoff: {NEWEST}"


def test_a_star_in_the_argument_is_split_not_GLOBBED(tmp_path, stub_bin):
    """ADVERSARIAL — the word split is an unquoted `$1`, so it is also a PATHNAME
    EXPANSION unless globbing is off.

    🔴 THE GLOB MUST BE ABLE TO PRODUCE A TOKEN THE SHAPE TEST WOULD ACCEPT, or
    this test is vacuous. It was: the original spelling used a bare `*`, which
    expanded to `README.md` — and once the shape test started rejecting
    `README.md` on its own merits, deleting `set -f` SURVIVED the whole suite
    while this test went on passing. A guard that keeps passing for a NEW reason
    has stopped guarding; only the mutation battery could see it.

    `claudedocs/*` is the spelling that still bites: it expands to real
    `claudedocs/handoff-*.md` paths, every one of which passes both halves of
    the shape test. Alphabetically first is ALPHA, which is deliberately not the
    newest — so a globbing implementation resolves ALPHA and this fails.
    """
    repo = make_repo(tmp_path, docs=("handoff-alpha-2026-01-01.md", WANTED, NEWEST))
    out = run_resume(repo, stub_bin, "resume the claudedocs/* work")
    assert handoff_line(out) != "handoff: handoff-alpha-2026-01-01.md", out
    assert handoff_line(out) == f"handoff: {NEWEST}", out


def test_a_handoff_shaped_name_OUTSIDE_claudedocs_is_not_taken(tmp_path, stub_bin):
    """ADVERSARIAL — pins the parent-directory half, which the basename half
    otherwise makes redundant. Deleting it SURVIVED the whole suite.

    It is not redundant, and this is the case that shows why: a handoff-SHAPED
    name outside `claudedocs/` is accepted without it — and because the path
    branch derives `$REPO` from the doc's own directory, a `/tmp/handoff-x.md`
    quoted in prose would retarget the ENTIRE digest away from the cwd's repo.
    Keeping the scan inside the repo's own convention bounds that.

    A caller who really does keep a handoff elsewhere still has the explicit
    path form, which takes any filename at all.
    """
    repo = make_repo(
        tmp_path, docs=(WANTED, NEWEST), files=("docs/handoff-notes-2026-05-05.md",)
    )
    bait = repo / "docs" / "handoff-notes-2026-05-05.md"
    out = run_resume(repo, stub_bin, f"resume; handoff: {bait}")
    assert handoff_line(out) == f"handoff: {NEWEST}", out


def test_an_explicit_path_still_beats_a_path_found_in_prose(tmp_path, stub_bin):
    """The bare-path branch must SURVIVE, not be subsumed by the prose scan.

    The filename carries a SPACE on purpose. For an ordinary path the two
    branches agree, so this would be an invariant guard rather than a test — and
    measured: merely REORDERING them (scan first, `-f "$arg"` as the fallback)
    is invisible to the whole suite, because the reordered code still answers
    correctly. A spaced name is the case only the whole-argument `[ -f "$arg" ]`
    test can serve: the prose scan word-splits it into `PICK` and `ME.md`,
    neither of which is a file.

    The mutation this was watched to kill is the plausible SIMPLIFICATION —
    deleting the `-f "$arg"` branch on the grounds that the scan covers it. That
    mutant is survived by both pre-existing explicit-path tests above; this is
    the case that catches it.
    """
    repo = two_initiative_repo(tmp_path)
    doc = repo / "claudedocs" / "PICK ME.md"
    doc.write_text("## pick me\n")
    out = run_resume(repo, stub_bin, str(doc))
    assert handoff_line(out) == "handoff: PICK ME.md"
    assert gap_lines(out) == [], out


# --------------------------------------------------------------------------- #
# 🔴 THE README CLASS — a `.md` token in prose is NOT a handoff reference
#
# The first version of `embedded_md_path` accepted any EXISTING `*.md` token,
# which converted #684's silent-wrong-document failure into a different one that
# fires on ordinary English. Measured on the shipped branch:
#
#   resume-state.sh "rewrite the README.md section then resume the listing work"
#     handoff: README.md
#     DRIFT  (none detected — live state matches the handoff's claims)
#
# The token must now name a member of the population resolve() itself globs —
# parent directory `claudedocs`, basename `handoff-*.md` or `*HANDOFF*.md`. Each
# case below is RED on the shipped branch and green here.
#
# `make_repo` tracks README.md at the repo root, which is the cwd of the run, so
# the first case needs no extra fixture — the bait is what every repo already
# has, which is exactly why the defect was reachable.
# --------------------------------------------------------------------------- #
def test_a_bare_md_token_in_prose_is_not_a_handoff_reference(tmp_path, stub_bin):
    """THE HEADLINE CASE. `README.md` exists in every repo this tool runs in."""
    repo = two_initiative_repo(tmp_path)
    out = run_resume(
        repo, stub_bin, "rewrite the README.md section then resume the listing work"
    )
    assert handoff_line(out) != "handoff: README.md", out
    assert handoff_line(out) == f"handoff: {NEWEST}"
    # …and because the argument did not resolve, it degrades LOUDLY.
    assert gap_lines(out), out


def test_a_backticked_md_path_outside_claudedocs_is_not_a_handoff_reference(
    tmp_path, stub_bin
):
    """Backticks are in the strip set, and code-quoting a path is the fleet's
    prose convention — so the convention made the defect MORE likely, not less.
    `subsystem_recall` harvests only backticked spans for exactly this reason.

    ⚠ NO COMMA AFTER THE CLOSING BACKTICK. The first draft wrote "`…md`," and
    passed at the shipped commit for an INCIDENTAL reason — the strip takes one
    character per side, so the token kept its backtick, failed `*.md`, and was
    rejected by accident rather than by the rule under test. Green for the wrong
    reason is the whole failure mode this round is about.
    """
    repo = make_repo(tmp_path, docs=(WANTED, NEWEST), files=("docs/ARCHITECTURE.md",))
    out = run_resume(repo, stub_bin, "see `docs/ARCHITECTURE.md` then resume")
    assert handoff_line(out) == f"handoff: {NEWEST}", out


def test_an_md_token_in_the_cwd_subdir_is_not_a_handoff_reference(tmp_path, stub_bin):
    """Run from a SUBDIRECTORY, where a relative `.md` token resolves against a
    directory that is not the repo root at all."""
    repo = make_repo(tmp_path, docs=(WANTED, NEWEST), files=("sub/keep.md",))
    out = run_resume(repo, stub_bin, "resume keep.md work", cwd=repo / "sub")
    assert handoff_line(out) == f"handoff: {NEWEST}", out


def test_a_single_token_md_ARGUMENT_is_the_explicit_path_branch(tmp_path, stub_bin):
    """CHARACTERISATION, and a CORRECTION to the audit — not a regression test.

    The audit listed "a root `wanted.md` shadowing a resolvable slug" alongside
    the README cases as damage from the prose scan. Measured on all three
    revisions with the same fixture, it is not:

        main 732db793   handoff: wanted.md
        #690 3b70baaa   handoff: wanted.md
        this branch     handoff: wanted.md

    `[ -f "$arg" ]` — the explicit-path branch, unchanged since long before
    #684 — takes a single-token argument that names an existing file, and that
    is the documented `handoff path` form. The prose scan cannot be responsible
    for it and never reaches it: for a SINGLE-token argument that exists, the
    path branch has already won, and one that does NOT exist fails the scan's
    own `-f` test. So the scan can shadow no slug that would otherwise resolve.

    Pinned here so the claim is checkable rather than argued: the slug
    `wanted.md` genuinely globs `claudedocs/handoff-wanted.md-plan.md`, and the
    root file still wins.
    """
    repo = make_repo(
        tmp_path,
        docs=("handoff-wanted.md-plan.md", NEWEST),
        files=("wanted.md",),
    )
    out = run_resume(repo, stub_bin, "wanted.md")
    assert handoff_line(out) == "handoff: wanted.md", out
    assert gap_lines(out) == [], out          # it RESOLVED; nothing to warn about


def test_a_stray_md_token_IN_PROSE_does_not_shadow_the_fallback(tmp_path, stub_bin):
    """The reachable half of that concern: a MULTI-token argument, where the
    stray `.md` is not the whole argument and the path branch never fires.

    On the shipped branch `wanted.md` was accepted and became the handoff. Now
    it is rejected, the argument resolves nothing, and the run degrades LOUDLY
    to the newest rather than quietly to a file that is not a handoff at all.
    """
    repo = make_repo(tmp_path, docs=(WANTED, NEWEST), files=("wanted.md",))
    out = run_resume(repo, stub_bin, "resume the wanted.md work please")
    assert handoff_line(out) == f"handoff: {NEWEST}", out
    assert gap_lines(out), out


@pytest.mark.parametrize("doc", DECOY_DOCS)
def test_a_decoy_doc_NAMED_IN_PROSE_is_not_a_handoff_reference(
    tmp_path, stub_bin, doc
):
    """Why the shape test is parent-dir AND basename, not OR.

    The audit proposed `claudedocs/` OR handoff-shaped. Under OR, every one of
    these nine resolves from prose — and this module already carries them as
    DECOY_DOCS precisely because they must never resolve as a handoff. They live
    IN claudedocs/; only the basename rule excludes them. `HANDBOOK.md` and
    `SHORTHAND-NOTES.md` are the ones that matter most: HAND, not HANDOFF.
    """
    repo = make_repo(tmp_path, docs=(WANTED, NEWEST, doc))
    arg = f"resume; see claudedocs/{doc} for background"
    assert handoff_line(run_resume(repo, stub_bin, arg)) == f"handoff: {NEWEST}", doc


def test_the_uppercase_family_form_DOES_resolve_from_prose(tmp_path, stub_bin):
    """POSITIVE CONTROL for the shape test's second glob.

    Without it the whole rule could be `handoff-*.md` and every case above would
    still pass — a filter that rejects everything is not the goal. This is the
    civitai-manager shape, quoted in prose.
    """
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md", NEWEST))
    arg = f"resume that work; handoff: {repo / 'claudedocs' / 'SESSION-HANDOFF.md'}"
    assert handoff_line(run_resume(repo, stub_bin, arg)) == "handoff: SESSION-HANDOFF.md"


def test_a_dot_is_required_before_md(tmp_path, stub_bin):
    """ADVERSARIAL — `handoff-*.md` -> `handoff-*md` survived the whole suite.

    `.mmd` is a mermaid diagram and ends in the letters `md`; it is a real thing
    to keep beside a handoff. Nothing else here can see the missing dot.
    """
    repo = make_repo(tmp_path, docs=(WANTED, NEWEST))
    bait = repo / "claudedocs" / "handoff-diagram.mmd"
    bait.write_text("graph TD;\n")
    out = run_resume(repo, stub_bin, f"resume; see {bait} for the shape")
    assert handoff_line(out) == f"handoff: {NEWEST}", out


def test_a_prose_path_RETARGETS_the_repo_to_the_docs_own_checkout(tmp_path, stub_bin):
    """ADVERSARIAL — deriving `$REPO` from `$PWD` instead of the doc's directory
    SURVIVED all 119 tests, and that retarget is the exact mechanism the
    `claudedocs/` bound exists to contain. Unpinned, it was invisible.

    The handoff names an initiative, and the initiative lives in ITS OWN repo —
    so the whole digest (branch, PRs, freshness) must follow the doc, not the
    directory the caller happened to be standing in.
    """
    other = make_repo(tmp_path, docs=("handoff-other-2026-06-06.md",), name="other-repo")
    here = two_initiative_repo(tmp_path)
    doc = other / "claudedocs" / "handoff-other-2026-06-06.md"
    out = run_resume(here, stub_bin, f"resume that; handoff: {doc}")
    assert handoff_line(out) == "handoff: handoff-other-2026-06-06.md", out
    repo_line = [ln for ln in out.splitlines() if ln.startswith("# repo:")][0]
    assert "other-repo" in repo_line, repo_line
    assert "fixture-repo" not in repo_line, repo_line


def test_a_directory_merely_ENDING_in_claudedocs_is_not_claudedocs(tmp_path, stub_bin):
    """ADVERSARIAL — `*/claudedocs|claudedocs` -> `*claudedocs` SURVIVED.

    The pattern must anchor on a whole path COMPONENT. Under the looser form,
    `myclaudedocs/` — or any `…-claudedocs` sibling — is accepted as the repo's
    handoff directory.
    """
    repo = make_repo(
        tmp_path,
        docs=(WANTED, NEWEST),
        files=("myclaudedocs/handoff-decoy-2026-07-07.md",),
    )
    bait = repo / "myclaudedocs" / "handoff-decoy-2026-07-07.md"
    out = run_resume(repo, stub_bin, f"resume; handoff: {bait}")
    assert handoff_line(out) == f"handoff: {NEWEST}", out


def test_a_DIRECTORY_named_like_a_handoff_is_not_taken(tmp_path, stub_bin):
    """ADVERSARIAL — pins `-f` against `-e`.

    A directory can be named `claudedocs/handoff-thing.md`, and it satisfies
    every shape rule; only the regular-file test excludes it. Under `-e` the run
    would resolve a directory as its handoff and every later block would read it.

    Since #1164 the rejection also stops the fallback: the token was named and
    is not a file, so nothing is reconciled. Under `-e` the digest would be
    non-empty and read a DIRECTORY, which is what this still separates.
    """
    repo = make_repo(tmp_path, docs=(WANTED, NEWEST))
    bait = repo / "claudedocs" / "handoff-adirectory-2026-04-04.md"
    bait.mkdir()
    out = run_resume(repo, stub_bin, f"resume; handoff: {bait}")
    assert handoff_line(out) == "handoff: (none found — git-only)", out
    assert gap_lines(out), out


def test_a_doc_matching_BOTH_globs_counts_as_one_candidate(tmp_path, stub_bin):
    """ADVERSARIAL — pins the `sort -u` in the candidate count.

    `handoff-HANDOFF.md` matches `handoff-*.md` AND `*HANDOFF*.md`, so a naive
    concatenation counts one file twice, crosses the >1 threshold, and warns in a
    repo that has exactly one handoff — reintroducing the permanently-red gate
    the count exists to prevent. The comment in resume-state.sh claims this
    explicitly; nothing checked it.
    """
    repo = make_repo(tmp_path, docs=("handoff-HANDOFF.md",))
    out = run_resume(repo, stub_bin, "no-such-topic")
    assert handoff_line(out) == "handoff: handoff-HANDOFF.md"
    # The miss itself is reported; what must NOT appear is the discarded-choice
    # clause, since this repo holds exactly one document.
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert "newest of" not in gaps[0], gaps


def test_the_FIRST_of_two_prose_paths_wins(tmp_path, stub_bin):
    """ADVERSARIAL — `break` -> `continue` (last match wins) survived all 94
    tests of the shipped branch, so the "First match wins" contract stated in
    the function's own comment was asserted NOWHERE.

    Both tokens are valid handoff references, so the shape test cannot decide
    this; only the loop's exit can.
    """
    repo = make_repo(tmp_path, docs=(WANTED, "handoff-second-2026-03-03.md", NEWEST))
    first = repo / "claudedocs" / WANTED
    second = repo / "claudedocs" / "handoff-second-2026-03-03.md"
    out = run_resume(repo, stub_bin, f"resume {first} and maybe {second}")
    assert handoff_line(out) == f"handoff: {WANTED}", out


def test_the_FIRST_of_two_MISSING_prose_paths_is_the_one_reported(
    tmp_path, stub_bin
):
    """ADVERSARIAL — recording the LAST missing token instead of the first
    SURVIVED the whole suite.

    The `miss` capture has to agree with the `hit` capture: first-wins. Both are
    "what the caller wrote", and reporting the second path back at someone who
    led with the first is a small lie in a message whose entire job is to say
    what they asked for.
    """
    repo = two_initiative_repo(tmp_path)
    first = repo / "claudedocs" / "handoff-first-gone-2026-01-01.md"
    second = repo / "claudedocs" / "handoff-second-gone-2026-02-02.md"
    gaps = gap_lines(run_resume(repo, stub_bin, f"resume {first} or {second}"))
    # ⚠ Scoped to the named-missing line. The OTHER gap echoes `$arg` verbatim,
    # so a naive search across all gaps finds BOTH paths and can never fail.
    named = [g for g in gaps if g.startswith("requested handoff ")]
    assert len(named) == 1, gaps
    assert "handoff-first-gone" in named[0], named
    assert "handoff-second-gone" not in named[0], named


def test_a_MISSING_token_does_not_preempt_a_REAL_one_later_in_the_sentence(
    tmp_path, stub_bin
):
    """The miss capture must never short-circuit the search.

    Recording a named-but-missing token is a consolation prize, taken only after
    the whole argument has been scanned without a hit. An implementation that
    returned as soon as it saw a handoff-shaped path that was not on disk would
    resolve NOTHING here and warn — worse than the behaviour it replaced, and
    invisible to every other test, all of which name one path.
    """
    repo = make_repo(tmp_path, docs=("handoff-real-2026-01-01.md", NEWEST))
    gone = repo / "claudedocs" / "handoff-gone-2026-01-01.md"
    real = repo / "claudedocs" / "handoff-real-2026-01-01.md"
    out = run_resume(repo, stub_bin, f"try {gone} then {real}")
    assert handoff_line(out) == "handoff: handoff-real-2026-01-01.md", out
    assert gap_lines(out) == [], out


def test_a_prose_path_wins_over_the_topic_named_in_the_same_sentence(
    tmp_path, stub_bin
):
    """CHARACTERISATION — and a CORRECTION to what this used to claim.

    It was called `…_BEATS_a_resolvable_slug` and described as pinning a
    precedence decision. It pins no such thing: the slug branch globs the WHOLE
    argument, so for the slug to resolve the argument must BE a bare topic, and
    for the prose scan to fire it must contain a `claudedocs/…` token — which a
    filename can never match, since it contains `/`. The two branches are
    structurally MUTUALLY EXCLUSIVE and the "precedence" is unreachable, which
    is why swapping their order survives the whole suite.

    What is real, and what this now asserts: a sentence that mentions one topic
    in prose while naming a different doc by path resolves the PATH.
    """
    repo = make_repo(tmp_path, docs=("handoff-alpha-2026-01-01.md", WANTED, NEWEST))
    doc = repo / "claudedocs" / WANTED
    out = run_resume(repo, stub_bin, f"alpha work; handoff: {doc}")
    assert handoff_line(out) == f"handoff: {WANTED}", out


# --------------------------------------------------------------------------- #
# 🔴 THE SEAM — `HANDOFF_GLOBS` is DECLARED the single source and was asserted
# NOWHERE
#
# `subsystem_recall.HANDOFF_GLOBS` is named as the source of truth by
# resume-state.sh's own comment, and both the fallback chain and the prose scan
# claim to mirror it. That is a RELATIONSHIP between a Python constant and two
# shell spellings, and nothing checked it — a "mirrors" claim with no guard is
# the class this file keeps finding.
# --------------------------------------------------------------------------- #
def test_the_shell_globs_mirror_HANDOFF_GLOBS_exactly():
    """A LEDGER, failing when the set grows OR shrinks OR is respelled.

    Not a keyword check: the whole basename patterns are compared as a set, so a
    case-folded or widened spelling on either side fails here rather than in
    production. `focus_window` and `resolve()` disagreeing about which files ARE
    handoffs is precisely how /resume ends up recalling one initiative while
    reconciling another.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
    import subsystem_recall  # noqa: PLC0415

    py = {g.split("/", 1)[1] for g in subsystem_recall.HANDOFF_GLOBS}
    assert {g.split("/", 1)[0] for g in subsystem_recall.HANDOFF_GLOBS} == {
        "claudedocs"
    }, subsystem_recall.HANDOFF_GLOBS

    src = (REPO_ROOT / "scripts/resume-state.sh").read_text(encoding="utf-8")
    # The fallback chain: `ls -t "$REPO"/claudedocs/<pattern>`
    # The slug branch globs `handoff-"$arg"*.md` from the SAME shape; it is
    # parameterised by the caller's topic and is not a member of the fallback
    # family, so drop anything carrying a shell expansion.
    chain = {
        p
        for p in re.findall(r'ls -t "\$REPO"/claudedocs/(\S+\.md)\b', src)
        if "$" not in p
    }
    # The prose scan's basename test: `case "$base" in a|b)`
    scan = set(re.search(r'case "\$base" in ([^)]+)\)', src).group(1).split("|"))

    assert py == chain, f"fallback chain {chain} != HANDOFF_GLOBS {py}"
    assert py == scan, f"prose scan {scan} != HANDOFF_GLOBS {py}"


def test_the_glob_seam_scanner_can_actually_find_the_patterns():
    """POSITIVE CONTROL. Two regexes over shell source is exactly the shape that
    silently matches nothing and passes by comparing two empty sets — the
    `assert py == chain` above would then be asserting `set() == set()` only if
    the Python side were also empty, so pin all three as NON-empty explicitly."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
    import subsystem_recall  # noqa: PLC0415

    src = (REPO_ROOT / "scripts/resume-state.sh").read_text(encoding="utf-8")
    # The slug branch globs `handoff-"$arg"*.md` from the SAME shape; it is
    # parameterised by the caller's topic and is not a member of the fallback
    # family, so drop anything carrying a shell expansion.
    chain = {
        p
        for p in re.findall(r'ls -t "\$REPO"/claudedocs/(\S+\.md)\b', src)
        if "$" not in p
    }
    scan = set(re.search(r'case "\$base" in ([^)]+)\)', src).group(1).split("|"))
    assert len(subsystem_recall.HANDOFF_GLOBS) >= 2
    assert len(chain) >= 2, "the fallback-chain regex matched nothing"
    assert len(scan) >= 2, "the prose-scan regex matched nothing"


def test_the_two_implementations_TIE_BREAK_differently_and_that_is_recorded():
    """🔴 A KNOWN, UNFIXED DIVERGENCE — recorded so it is not rediscovered as a
    surprise, and so nobody reads the seam guard above as covering more than it
    does.

    The glob SETS match. The ORDER within them does not: `focus_window` sorts by
    `(mtime, name)` in Python, while resume-state.sh takes `ls -t | head -1`.
    On equal mtimes the two can pick different files. It does not matter for the
    set-level claim the guard above makes, and fixing it changes `focus_window`'s
    behaviour, so it is deliberately out of scope here — but it IS the same
    "step 3 vs step 4" seam, and it is real.
    """
    src = (REPO_ROOT / "scripts/lib/subsystem_recall.py").read_text(encoding="utf-8")
    assert "focus_window" in src
    shell = (REPO_ROOT / "scripts/resume-state.sh").read_text(encoding="utf-8")
    assert "ls -t" in shell, (
        "resume-state.sh no longer uses `ls -t`; if the ordering was unified with "
        "focus_window, delete this test and say so — do not just re-point it."
    )


# --------------------------------------------------------------------------- #
# 🔴 THE GAP IS ABOUT AN UNATTRIBUTABLE CHOICE, NOT ABOUT THE MISS
#
# The first version warned whenever the slug glob missed, which made it
# PERMANENTLY RED in the very repo the caps fallback exists for: civitai-manager
# holds one handoff, so `resume-state.sh session` printed a GAPS banner and "NOT
# a clean bill of health" every single run. A gate that is red on every run
# trains the reader to skip the GAPS block — which destroys the value of the gap
# this change exists to add.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "docs,resolved,family",
    [
        # 🔴 THE BOUNDARY, BOTH SIDES. Keying the whole warning on the count made
        # the family==1 row SILENT — a supplied topic, no match, a different
        # document reconciled under "(none detected — live state matches the
        # handoff's claims)". That is issue #684's own reproduction.
        pytest.param(("handoff-alpha-2026-01-01.md", "SESSION-HANDOFF.md"),
                     "handoff-alpha-2026-01-01.md", 1, id="family-1"),
        pytest.param(("handoff-alpha-2026-01-01.md", "handoff-beta-2026-02-02.md"),
                     "handoff-beta-2026-02-02.md", 2, id="family-2"),
    ],
)
def test_a_slug_that_matches_NOTHING_always_warns(
    tmp_path, stub_bin, docs, resolved, family
):
    """A supplied topic that resolved nothing is reported WHATEVER the count.

    The count answers "did the fallback have to CHOOSE?" — a different question
    from "did the caller ask for something the tool then overrode?". Only the
    "newest of N … MOVES between runs" clause depends on it, because only that
    clause is false when nothing was discarded.
    """
    repo = make_repo(tmp_path, docs=docs)
    out = run_resume(repo, stub_bin, "no-such-topic-at-all")
    assert handoff_line(out) == f"handoff: {resolved}"
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert "no-such-topic-at-all" in gaps[0], gaps
    assert "matches the handoff's claims" not in " ".join(drift_lines(out)), out
    # …and the conditional clause appears on exactly the side where it is true.
    if family >= 2:
        assert f"newest of {family}" in gaps[0], gaps
        assert "MOVES between runs" in gaps[0], gaps
    else:
        assert "newest of" not in gaps[0], gaps
        assert "MOVES" not in gaps[0], gaps


def test_the_slug_that_RESOLVES_and_the_no_argument_run_stay_silent(
    tmp_path, stub_bin
):
    """THE CONTRACT the rule above must not swallow.

    Warning unconditionally on a MISS is right; warning on a HIT, or on a run
    that asked for nothing, would make the GAPS block permanently red and train
    the reader to skip it. Both sides in one place so the pair cannot drift.
    """
    repo = make_repo(tmp_path, docs=("handoff-alpha-2026-01-01.md", NEWEST))
    hit = run_resume(repo, stub_bin, "alpha")
    assert handoff_line(hit) == "handoff: handoff-alpha-2026-01-01.md"
    assert gap_lines(hit) == [], hit
    noarg = run_resume(repo, stub_bin)
    assert handoff_line(noarg) == f"handoff: {NEWEST}"
    assert gap_lines(noarg) == [], noarg
    assert "matches the handoff's claims" in " ".join(drift_lines(noarg)), noarg


def test_a_TWO_candidate_repo_still_warns_and_says_how_many(tmp_path, stub_bin):
    """The other side of the same boundary — measured at 1 and at 2, because a
    rule that depends on a count is only pinned if both sides of it are."""
    repo = two_initiative_repo(tmp_path)
    gaps = gap_lines(run_resume(repo, stub_bin, "no-such-topic"))
    assert gaps, "two candidates and no warning"
    assert "newest of 2" in " ".join(gaps), gaps


def test_the_count_follows_the_CAPS_family_when_that_is_what_resolved(
    tmp_path, stub_bin
):
    """ADVERSARIAL — counting only `handoff-*.md` SURVIVED the whole suite.

    Both handoffs here are caps-family, so a lowercase-only count returns 0,
    never crosses the threshold, and the warning silently stops firing in
    exactly the repos the caps fallback was written for. The fallback genuinely
    chose between two docs.
    """
    repo = make_repo(tmp_path, docs=("HANDOFF-old-2026-01-01.md", "SESSION-HANDOFF.md"))
    out = run_resume(repo, stub_bin, "no-such-topic")
    assert handoff_line(out) == "handoff: SESSION-HANDOFF.md"
    gaps = gap_lines(out)
    assert gaps, "two caps-family candidates and no warning"
    assert "newest of 2" in " ".join(gaps), gaps


def test_a_MIXED_family_repo_counts_only_the_family_that_resolved(tmp_path, stub_bin):
    """🔴 THE UNION OVERSTATES, AND THE SENTENCE IT PRODUCES IS FALSE.

    One lowercase doc and one caps doc. The lowercase glob has exactly one
    member and decides; the caps glob is never reached. So the choice is
    DETERMINISTIC — yet a union count says "the newest of 2 … MOVES between
    runs", which breaks the rule written directly above that message ("EVERY
    CLAUSE BELOW MUST BE TRUE OF EVERY RUN THAT REACHES IT") and re-creates the
    spurious-warning shape the count was added to remove.

    Its sibling above is blind to this: 2 caps + 0 lowercase counts the same
    either way. Both fixtures are needed; neither alone pins the rule.
    """
    repo = make_repo(
        tmp_path, docs=("handoff-alpha-2026-01-01.md", "SESSION-HANDOFF.md")
    )
    out = run_resume(repo, stub_bin, "no-such-topic")
    assert handoff_line(out) == "handoff: handoff-alpha-2026-01-01.md"
    # The miss is still reported — what the family count controls is only the
    # "newest of N … MOVES" clause, and here it would be a false statement.
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert "newest of" not in gaps[0], gaps
    assert "MOVES" not in gaps[0], gaps


# --------------------------------------------------------------------------- #
# 🔴 EVERY SHAPE THE GAP CAN TAKE IS PINNED WHOLE — not one of them.
#
# The artifact under test is PROSE, so a guard on words is walkable by
# rewording; the fix is to pin the entire normalised string. That rule was
# applied to ONE shape, and the block can emit SIX (two leads x {no-fallback,
# fallback} x {MOVES, no MOVES}). The other five were asserted only by substring
# or by absence — and that is exactly how a FALSE clause shipped in a change
# whose thesis is honest messaging:
#
#     "... FELL BACK to handoff-alpha-2026-01-01.md, a DIFFERENT document from
#      the one you asked for"
#
# emitted for `resume-state.sh handoff-alpha-2026-01-01.md` — the same filename
# on both sides of the sentence, called different — and for
# `resume-state.sh session` in a repo whose only doc IS SESSION-HANDOFF.md, the
# invocation resolve()'s own comment blesses by name.
#
# The clause was an IDENTITY claim; the tool only has evidence for a MECHANICAL
# one. It is gone. What remains is what is true on every run that reaches it.
#
# Assembled from parts so the six expectations cannot drift apart, and so a
# reworded clause fails in exactly one place.
# --------------------------------------------------------------------------- #
GAP_LEAD_MISSING = 'requested handoff "{tok}" — NO SUCH FILE (renamed, moved, or in another checkout?).'
# The #1164 lead: the named path is not on disk AND several worktrees of its own
# clone hold that basename, so none was chosen. It REPLACES the lead above
# rather than adding a second line — one cause, one line.
#
# 🔴 "of that clone" was RETIRED (audit of #1197, F2): the candidates come from
# whichever clone the search used, and for a bare `claudedocs/<base>` token the
# caller named no clone at all, so "that" had no antecedent. Every clause here
# must be true of every run that reaches it.
GAP_LEAD_AMBIGUOUS = 'requested handoff "{tok}" — NO SUCH FILE, and {base} exists in {n} worktrees of the clone that path resolves against ({paths}), so NONE was chosen.'
GAP_LEAD_SLUG = 'requested "{arg}" — nothing in it resolved to a handoff doc under {repo}/claudedocs.'
GAP_REST_NONE = " NOTHING was reconciled; the DRIFT section below is about no document at all."
GAP_REST_FELL = " The digest FELL BACK to {name}.{moves} Re-run naming the doc's path, or with no argument to take newest deliberately."
GAP_MOVES = " It is the newest of {n}, and which one that is depends on commit times, so it MOVES between runs."


def _repo_as_the_script_resolved_it(out):
    """`$REPO` from the digest itself, not as the fixture spells it — a
    symlinked tmpdir would otherwise fail these on the path alone."""
    line = [ln for ln in out.splitlines() if ln.startswith("# repo:")][0]
    return line.split("# repo:", 1)[1].split("slug:")[0].strip()


@pytest.mark.parametrize(
    "docs,kind,n_moves,resolved",
    [
        # slug lead x fallback, without and with the MOVES clause
        pytest.param(("SESSION-HANDOFF.md",), "slug", 0, "SESSION-HANDOFF.md",
                     id="slug-fell-back-1"),
        pytest.param((WANTED, NEWEST), "slug", 2, NEWEST, id="slug-fell-back-2"),
        # 🔴 THE NAMED-MISSING LEAD NEVER PAIRS WITH THE FALLBACK REST (#1164).
        # These two rows used to expect `GAP_REST_FELL` — one candidate and two
        # — because the chain ran for a named path as well. It does not any
        # more, so both now pin the no-fallback sentence WITH docs on disk that
        # the run could have taken. The `resolved=None` rows below are the
        # empty-repo shape; these are the "there was something to steal and it
        # was not stolen" shape, which is the one #1164 is about.
        pytest.param(("SESSION-HANDOFF.md",), "missing", 0, None,
                     id="missing-does-not-fall-back-1"),
        pytest.param((WANTED, NEWEST), "missing", 0, None,
                     id="missing-does-not-fall-back-2"),
        # …and the two branches where NOTHING resolved. 🔴 F2: this is the
        # strongest honesty claim in the feature and it had no whole-string pin —
        # only a substring assert — so replacing it with the FALSE "The digest
        # FELL BACK to nothing at all." passed 138/138.
        pytest.param((), "slug", 0, None, id="slug-nothing-resolved"),
        pytest.param((), "missing", 0, None, id="missing-nothing-resolved"),
    ],
)
def test_every_gap_sentence_is_pinned_WHOLE(
    tmp_path, stub_bin, docs, kind, n_moves, resolved
):
    repo = make_repo(tmp_path, docs=docs)
    gone = repo / "claudedocs" / "handoff-gone-2026-01-01.md"
    arg = f"resume it; handoff: {gone}" if kind == "missing" else "no-such-topic"

    out = run_resume(repo, stub_bin, arg)
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps

    where = _repo_as_the_script_resolved_it(out)
    lead = (
        GAP_LEAD_MISSING.format(tok=gone)
        if kind == "missing"
        else GAP_LEAD_SLUG.format(arg=arg, repo=where)
    )
    if resolved is None:
        rest = GAP_REST_NONE
    else:
        moves = GAP_MOVES.format(n=n_moves) if n_moves else ""
        rest = GAP_REST_FELL.format(name=resolved, moves=moves)
    assert gaps[0] == lead + rest


def test_the_whole_sentence_pins_can_actually_FAIL():
    """POSITIVE CONTROL for the six assertions above.

    They are equality checks against strings assembled from module constants —
    if a template ever drifted to match whatever the script emits (or a helper
    returned the observed value), they would pass by construction. This proves
    the templates carry real, distinct content rather than being empty or equal.
    """
    parts = [GAP_LEAD_MISSING, GAP_LEAD_AMBIGUOUS, GAP_LEAD_SLUG, GAP_REST_NONE,
             GAP_REST_FELL, GAP_MOVES]
    assert len(set(parts)) == len(parts)
    assert all(len(p) > 40 for p in parts), parts
    # 🔴 The identity claim retired by F1 must not creep back into any template.
    for p in parts:
        assert "DIFFERENT document" not in p, p
        assert "nothing below is scoped" not in p, p

# A handoff body that names PRs the stubbed `gh` cannot answer for, so a run
# gets a PR gap IN ADDITION to whatever the resolution produces. Defined here
# rather than reusing PR_HANDOFF below, because a @parametrize decorator is
# evaluated at IMPORT time and that constant is defined further down the file.
PR_REFERENCING_BODY = (
    "## Handoff\n"
    "PR #4101 is OPEN and awaiting review. #4102 is also in-flight.\n"
)


@pytest.mark.parametrize(
    "arg,docs,body,want",
    [
        pytest.param("no-such-topic", (WANTED, NEWEST), None, 1, id="slug-miss"),
        pytest.param("PROSE_MISSING_PATH", (WANTED, NEWEST), None, 1, id="named-missing"),
        pytest.param("PROSE_MISSING_PATH", ("SESSION-HANDOFF.md",), None, 1,
                     id="named-missing-1"),
        pytest.param("no-such-topic", ("SESSION-HANDOFF.md",), None, 1, id="slug-miss-1"),
        pytest.param("no-such-topic", (), None, 1, id="nothing-to-fall-back-to"),
        # 🔴 THE ROW THAT MAKES THE ASSERTION MEAN ANYTHING. Every row above
        # yields exactly ONE gap, so a header hardcoded to `GAPS (1)` satisfies
        # `declared == len(lines)` in all of them — measured: that mutant
        # SURVIVED the whole suite. This doc also references PRs the stubbed gh
        # cannot answer for, so the resolution gap and the PR gap stack and the
        # count has to move off 1.
        pytest.param("no-such-topic", (WANTED, NEWEST), PR_REFERENCING_BODY, 2, id="two-gaps"),
    ],
)
def test_the_GAPS_header_count_equals_the_number_of_lines_printed(
    tmp_path, stub_bin, arg, docs, body, want
):
    """🔴 THE HEADER IS EVIDENCE, so it has to agree with the body.

    `!! GAPS (N)` is read as a count of findings — two audits of this PR used it
    as evidence — so a header that can disagree with what is printed undermines
    the block it introduces. Structurally N is `${#UNRECONCILED[@]}` and the body
    is one `printf` per element, which is why this holds; the point of asserting
    it is that the resolution paths must keep feeding ONE element per cause.

    This is what a single cause emitting TWO near-duplicate lines looked like
    from the outside, and why that was consolidated to one append site: the
    reader cannot tell "two findings" from "one finding, printed twice", and the
    cheapest reading of the difference is that the count is broken.
    """
    repo = make_repo(tmp_path, docs=docs, doc_body=body)
    if arg == "PROSE_MISSING_PATH":
        gone = repo / "claudedocs" / "handoff-gone-2026-01-01.md"
        arg = f"resume it; handoff: {gone}"
    out = run_resume(repo, stub_bin, arg)
    lines = gap_lines(out)
    banner = [ln for ln in drift_lines(out) if ln.startswith("!! GAPS")]
    assert len(banner) == 1, drift_lines(out)
    declared = int(re.search(r"GAPS \((\d+)\)", banner[0]).group(1))
    assert declared == len(lines), f"header says {declared}, printed {len(lines)}: {lines}"
    # POSITIVE CONTROL, in two parts. A zero on both sides satisfies the equality
    # while proving nothing — and so does a suite in which the count is ALWAYS 1,
    # which is why `want` is asserted per row rather than just `>= 1`.
    assert declared == want, f"expected {want} gap(s), got {declared}: {lines}"
    # …and no cause may print the same sentence twice.
    assert len(set(lines)) == len(lines), lines


# --------------------------------------------------------------------------- #
# THE FALSE GREEN — the DRIFT section must not claim a reconciliation it
# never performed
# --------------------------------------------------------------------------- #
def test_drift_is_honest_when_no_handoff_loaded(tmp_path, stub_bin):
    """The git-only case reconciled against nothing, so it may not say the live
    state "matches the handoff's claims" — there is no handoff."""
    repo = make_repo(tmp_path, docs=DECOY_DOCS)
    out = run_resume(repo, stub_bin)
    assert handoff_line(out) == "handoff: (none found — git-only)"
    lines = drift_lines(out)
    assert lines, f"DRIFT section was empty\n{out}"
    joined = " ".join(lines)
    assert "no handoff" in joined, f"DRIFT did not say a handoff was missing: {lines}"
    assert "matches the handoff's claims" not in joined, (
        f"DRIFT claimed a reconciliation that never happened: {lines}"
    )


def test_drift_keeps_its_clean_message_when_a_handoff_did_load(tmp_path, stub_bin):
    """The honest-git-only wording must not leak onto the real clean path.

    This handoff references NO PRs, so gh has nothing to answer and its absence
    costs no coverage — the clean line is legitimate here. That distinction is
    the point of test_drift_is_honest_when_gh_answered_for_nothing below: this
    test may only assert the clean line on a run that had nothing to reconcile
    remotely, never on one where a source failed.
    """
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",))
    out = run_resume(repo, stub_bin)
    assert handoff_line(out) == "handoff: SESSION-HANDOFF.md"
    joined = " ".join(drift_lines(out))
    assert "matches the handoff's claims" in joined, joined
    assert "no handoff" not in joined, joined
    assert "did not answer" not in joined, joined


# --------------------------------------------------------------------------- #
# 🔴 THE SAME FALSE GREEN ONE LAYER UP — a handoff loaded, but the source that
# was supposed to reconcile it never answered
# --------------------------------------------------------------------------- #
PR_HANDOFF = (
    "## Handoff\n"
    "PR #4101 is OPEN and awaiting review. #4102 is also in-flight.\n"
    "See https://github.com/acme/widget/pull/4103 for the follow-on.\n"
)


def test_drift_is_honest_when_gh_answered_for_nothing(tmp_path, stub_bin):
    """gh present, remote present, every `gh pr view` FAILS (offline / unauth /
    rate-limited / no access) — the loop's `|| continue` swallows all of it.

    Before the fix this printed no diagnostic at all and DRIFT still claimed
    live state "matches the handoff's claims" — the same sentence and the same
    harm class as the missing-handoff case this PR set out to remove.
    """
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=PR_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(repo, stub_bin)
    assert handoff_line(out) == "handoff: SESSION-HANDOFF.md"
    joined = " ".join(drift_lines(out))
    assert "matches the handoff's claims" not in joined, (
        f"DRIFT claimed a clean reconciliation while gh answered for nothing: {joined}"
    )
    assert "did not answer" in joined, joined
    # and it must say HOW MUCH went unchecked, not merely that something did
    assert "0 of 3" in joined, joined


def test_the_unanswered_source_is_reported_alongside_real_findings(tmp_path, stub_bin):
    """A gap must not hide behind findings. With real drift on screen, a list of
    findings reads as complete unless the incompleteness is stated next to it."""
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=PR_HANDOFF + "Also on feat/gone-branch.\n",
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(repo, stub_bin)
    lines = drift_lines(out)
    assert any("feat/gone-branch" in ln for ln in lines), lines
    assert any("did not answer" in ln or "0 of 3" in ln for ln in lines), lines


def test_a_handoff_naming_no_prs_is_not_downgraded_by_gh_being_absent(tmp_path, stub_bin):
    """Fail-open in the honest direction: if the handoff references no PRs there
    is nothing for gh to answer, so its absence must NOT downgrade the verdict.
    Otherwise every non-GitHub repo reads as permanently unreconciled and the
    warning becomes noise people learn to ignore."""
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body="## Handoff\nNo pull requests are referenced here at all.\n",
        remote="git@github.com:acme/widget.git",
    )
    joined = " ".join(drift_lines(run_resume(repo, stub_bin)))
    assert "matches the handoff's claims" in joined, joined
    assert "did not answer" not in joined, joined


def test_no_remote_with_referenced_prs_is_reported_as_unreconciled(tmp_path, stub_bin):
    """The OTHER unreconciled path: nothing can be resolved because there is no
    remote, so no repo can claim the two bare refs. The handoff still names PRs
    that nobody checked, so the verdict must degrade exactly as it does when gh
    runs and fails. Untested until the mutation battery pointed at this branch.

    ⚠ The WORDING here changed with the cross-repo fix and the change is the
    point. `acme/widget/pull/1141`-style qualified refs are now resolvable with
    no local remote at all, so "no remote" is no longer a blanket reason to
    check nothing — only the BARE refs become unattributable, and the digest
    now says which of the two happened instead of merging them.
    """
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",), doc_body=PR_HANDOFF)
    out = run_resume(repo, stub_bin)
    joined = " ".join(drift_lines(out))
    assert "matches the handoff's claims" not in joined, joined
    assert "did not answer" in joined, joined
    # the two bare refs (#4101/#4102) cannot be attributed to any repo here
    assert "PR UNATTRIBUTED" in out and "#4101" in out and "#4102" in out, out
    assert "2 bare #N ref(s) could not be attributed" in joined, joined


def test_a_partial_gh_answer_is_reported_as_partial(tmp_path, stub_bin):
    """gh answers for SOME referenced PRs and fails for the rest.

    This is the case the code's own comment names — "one unreachable PR hides
    behind four that worked" — and it was UNGUARDED: mutating
    `elif [ "$n_ok" -lt "$n_try" ]` to `-lt 0` survived all 46 tests, because
    the fixtures could only make gh answer for all or none. The selective stub
    exists for this branch.
    """
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=PR_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(
        repo,
        stub_bin,
        extra_env={
            "STUB_GH_JSON": '{"state":"OPEN","statusCheckRollup":[]}',
            "STUB_GH_OK_PRS": "4101 4102",       # 4103 fails
        },
    )
    # precondition: the fixture really did reach the PARTIAL case, not all-or-none
    assert "PR #4101 OPEN" in out and "PR #4102 OPEN" in out, out
    assert "PR #4103" not in out, out
    joined = " ".join(drift_lines(out))
    assert "2 of 3" in joined, joined
    assert "matches the handoff's claims" not in joined, joined
    assert "did not answer" in joined, joined


def test_drift_is_clean_when_gh_actually_answers(tmp_path, stub_bin):
    """POSITIVE CONTROL for the two tests above.

    They assert the presence of a warning; this proves the warning is driven by
    the reconciliation actually failing rather than being unconditional — with
    the same handoff and a gh that ANSWERS, the clean line comes back. Without
    this, hard-coding the warning would pass both.
    """
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=PR_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(
        repo,
        stub_bin,
        extra_env={"STUB_GH_JSON": '{"state":"OPEN","statusCheckRollup":[]}'},
    )
    assert "PR #4101 OPEN" in out, out
    joined = " ".join(drift_lines(out))
    assert "did not answer" not in joined, joined
    assert "matches the handoff's claims" in joined, joined


# --------------------------------------------------------------------------- #
# 🔴 CROSS-REPO PR REFS — a `#N` carrying an `owner/repo` qualifier must be
# resolved against THAT repo, never against whatever repo happens to be cwd
# --------------------------------------------------------------------------- #
# The real doc's shape, measured 2026-08-20 in datapacket-talos: four qualified
# refs into three other repos, one qualified ref into this one, one bare ref,
# and two decoys (a 1-digit prose ref and a markdown line anchor) that are not
# PRs at all. Every ref is framed as in-flight, so any ref the script resolves
# and finds MERGED becomes a DRIFT line — which is what makes a misattribution
# visible rather than silent.
CROSS_REPO_HANDOFF = (
    "## Handoff\n"
    "- `civitai/cli#423` is OPEN and awaiting review.\n"
    "- `civitai/civitai#4158` is OPEN, mergeable, blocked on the schema bump.\n"
    "- `civitai/civitai-app-starters#247` is still in draft, pending a decision.\n"
    "- `civitai/civitai-orchestration#311` is in flight, CI pending.\n"
    "Landed here: https://github.com/acme/widget/pull/1141 — already deployed.\n"
    "Also still open: #4114 awaits the next cut.\n"
    "A stray prose ref like #5, and the anchor `claudedocs/notes.md#12`.\n"
)

MERGED_JSON = '{"state":"MERGED","mergeable":"MERGEABLE","statusCheckRollup":[]}'


def gh_targets(log):
    """Every `-R <slug>` the script actually handed to gh, in order.

    🔴 THE ASSERTION THAT MATTERS. Checking only the printed label would pass a
    change that pretty-prints `civitai/cli#423` while still querying the local
    repo — the label and the lookup are independent, and it is the LOOKUP that
    produced the false findings. This reads the lookup.
    """
    out = []
    if not log.exists():
        return out
    for ln in log.read_text().splitlines():
        parts = ln.split()
        if "-R" in parts:
            out.append(parts[parts.index("-R") + 1])
    return out


def test_a_qualified_ref_is_resolved_against_its_own_repo(tmp_path, stub_bin):
    """THE REGRESSION.

    RED before the fix: `extract_prs` stripped the qualifier, so all four
    foreign refs were looked up in acme/widget and — because the stub answers
    MERGED for everything, exactly as the real gh did for talos-infra's own
    unrelated PRs of those numbers — DRIFT emitted a fabricated
    "MERGED but handoff frames it as open/in-flight (do the follow-on)" for
    each. A confident instruction to act on a premise that does not exist.
    """
    _, log = stub_bin
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=CROSS_REPO_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(repo, stub_bin, extra_env={"STUB_GH_JSON": MERGED_JSON})

    assert sorted(gh_targets(log)) == [
        "acme/widget",
        "civitai/civitai",
        "civitai/civitai-app-starters",
        "civitai/civitai-orchestration",
        "civitai/cli",
    ], gh_targets(log)

    # and the findings name the repo, so a reader can act on them
    joined = " ".join(drift_lines(out))
    for ref in (
        "civitai/cli#423",
        "civitai/civitai#4158",
        "civitai/civitai-app-starters#247",
        "civitai/civitai-orchestration#311",
    ):
        assert ref in joined, f"{ref} missing from DRIFT: {joined}"


@pytest.mark.parametrize("num", ["423", "4158", "247", "311"])
def test_a_foreign_ref_is_never_looked_up_in_the_local_repo(tmp_path, stub_bin, num):
    """The precise defect, one ref at a time: no foreign PR number may ever be
    paired with the LOCAL slug in a gh call. Reads the invocation log, so it
    fails on the lookup rather than on the cosmetics of the printed line."""
    _, log = stub_bin
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=CROSS_REPO_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    run_resume(repo, stub_bin, extra_env={"STUB_GH_JSON": MERGED_JSON})
    calls = [ln for ln in log.read_text().splitlines() if f" {num} " in ln]
    assert calls, f"#{num} was never looked up at all"
    for ln in calls:
        assert "-R acme/widget" not in ln, f"#{num} was resolved against the LOCAL repo: {ln}"


def test_a_bare_ref_is_unattributed_when_the_doc_names_other_repos(tmp_path, stub_bin):
    """`#4114` is bare in a doc that cites four other repos, so nothing in the
    text says which repo owns it. It must be REPORTED as unattributed and never
    resolved — a silent skip would rebuild the same false green one layer down.
    """
    _, log = stub_bin
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=CROSS_REPO_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(repo, stub_bin, extra_env={"STUB_GH_JSON": MERGED_JSON})
    assert "PR UNATTRIBUTED" in out and "#4114" in out, out
    # collapsed onto ONE line: a real doc carries dozens of bare refs and a
    # line each is its own wall of noise
    assert len([l for l in out.splitlines() if "UNATTRIBUTED" in l]) == 1, out
    assert "4114" not in " ".join(gh_targets(log)), gh_targets(log)
    for ln in log.read_text().splitlines():
        assert " 4114 " not in ln, f"an unattributable ref was resolved anyway: {ln}"
    # reported, not swallowed: it degrades the verdict and prints as a gap
    joined = " ".join(drift_lines(out))
    assert "could not be attributed" in joined, joined
    assert "matches the handoff's claims" not in joined, joined


def test_a_bare_ref_IS_claimed_when_the_doc_names_only_this_repo(tmp_path, stub_bin):
    """POSITIVE CONTROL for the test above — and the guard against overshooting.

    Refusing to attribute is only correct when the doc is genuinely ambiguous.
    The overwhelmingly common handoff cites its OWN repo and nothing else, and
    there a bare `#N` is unambiguous; a fix that made every bare ref
    unattributed would gut the tool while passing every test above.
    """
    _, log = stub_bin
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=PR_HANDOFF,          # bare #4101/#4102 + an acme/widget pull URL
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(repo, stub_bin, extra_env={"STUB_GH_JSON": MERGED_JSON})
    assert "UNATTRIBUTED" not in out, out
    assert sorted(set(gh_targets(log))) == ["acme/widget"], gh_targets(log)
    assert "PR #4101" in out and "PR #4102" in out, out


def test_a_qualified_ref_to_THIS_repo_does_not_make_bare_refs_ambiguous(
    tmp_path, stub_bin
):
    """A doc may spell its own repo out — `acme/widget#7` — without that making
    its bare refs ambiguous. Only a FOREIGN repo does. Mutating the awk filter
    to flag any qualified ref (dropping the `tolower($1)!=tolower(me)` test)
    passes every other test in this file and fails here."""
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body="## Handoff\nacme/widget#77 is OPEN. So is #4101.\n",
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(repo, stub_bin, extra_env={"STUB_GH_JSON": MERGED_JSON})
    assert "UNATTRIBUTED" not in out, out


def test_a_markdown_line_anchor_is_not_a_pr(tmp_path, stub_bin):
    """`claudedocs/notes.md#12` is a line anchor. The old code took the `#12`
    as a bare PR and looked it up; it appeared in the real run's output."""
    _, log = stub_bin
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=CROSS_REPO_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    out = run_resume(repo, stub_bin, extra_env={"STUB_GH_JSON": MERGED_JSON})
    assert "#12 " not in out and "PR #12" not in out, out
    assert "claudedocs/notes.md" not in " ".join(gh_targets(log)), gh_targets(log)


# --------------------------------------------------------------------------- #
# 🔴 GAP PROMINENCE — a `!` line beneath a wall of `-` findings gets missed
# --------------------------------------------------------------------------- #
def test_gaps_print_under_a_shouted_banner_beside_real_findings(tmp_path, stub_bin):
    """Measured 2026-08-20: the `!` gap lines were formatted exactly like the
    `-` finding lines and were duly read straight past. They now carry a rule
    and a header naming the count, while KEEPING the `!` prefix the /resume
    skill keys on."""
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body=CROSS_REPO_HANDOFF,
        remote="git@github.com:acme/widget.git",
    )
    lines = drift_lines(run_resume(repo, stub_bin, extra_env={"STUB_GH_JSON": MERGED_JSON}))
    assert any(ln.startswith("- ") for ln in lines), lines      # real findings present
    assert any(ln.startswith("! ") for ln in lines), lines      # prefix preserved
    banner = [ln for ln in lines if ln.startswith("!!")]
    assert banner, f"no gap banner beside the findings: {lines}"
    assert "GAPS (1)" in banner[0], banner
    assert any("═" in ln for ln in lines), lines


# --------------------------------------------------------------------------- #
# 🔴 A HANDOFF READ OUT OF A STALE WORKING TREE
#
# These fixtures use a REAL bare repo on disk as `origin`, so the script's
# `git fetch` runs for real with no network — RESUME_STATE_SKIP_FETCH is
# cleared. That is deliberate: mocking the fetch away would leave the whole
# freshness path unexercised, which is how it got shipped broken elsewhere.
# --------------------------------------------------------------------------- #
def make_stale_clone(tmp_path, local_lines=8, origin_extra=276, dirty=False):
    """A clone whose checked-out handoff is `origin_extra` lines behind origin.

    Built the way the real thing happens: clone A commits v1 and pushes, clone
    B pushes a much longer v2, and clone A is simply never updated. A's
    `origin/main` ref is stale too until the script fetches — which is the
    point, and what a comparison against a local ref alone would miss.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)],
        check=True, env=_git_env(tmp_path),
    )
    env = _git_env(tmp_path)
    a = tmp_path / "cloneA"
    b = tmp_path / "cloneB"
    for dest in (a, b):
        subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, env=env)

    doc = "claudedocs/handoff-stale.md"
    v1 = "# Handoff\n" + "".join(f"old line {i}\n" for i in range(local_lines))
    (a / "claudedocs").mkdir(parents=True)
    (a / doc).write_text(v1)
    ga = ["git", "-C", str(a)]
    subprocess.run([*ga, "add", doc], check=True, env=_git_env(a))
    subprocess.run([*ga, "commit", "-qm", "handoff v1"], check=True, env=_git_env(a))
    subprocess.run([*ga, "push", "-q", "origin", "main"], check=True, env=_git_env(a))

    gb = ["git", "-C", str(b)]
    subprocess.run([*gb, "pull", "-q", "origin", "main"], check=True, env=_git_env(b))
    v2 = v1 + "".join(
        f"NEW finding {i} — written by the last session\n" for i in range(origin_extra)
    )
    # origin_extra=0 is the UP-TO-DATE control, so there is deliberately nothing
    # to commit here — `git commit` would exit 1 on an empty tree.
    if origin_extra:
        (b / doc).write_text(v2)
        subprocess.run([*gb, "add", doc], check=True, env=_git_env(b))
        subprocess.run([*gb, "commit", "-qm", "handoff v2"], check=True, env=_git_env(b))
        subprocess.run([*gb, "push", "-q", "origin", "main"], check=True, env=_git_env(b))

    if dirty:
        (a / doc).write_text(v1 + "an uncommitted note from THIS session\n")
    return a, v1, v2


def read_line(stdout):
    hits = [ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("handoff-read:")]
    assert len(hits) == 1, f"expected one handoff-read: line, got {hits}\n{stdout}"
    return hits[0]


def test_a_stale_working_tree_handoff_is_detected_and_the_origin_copy_is_read(
    tmp_path, stub_bin
):
    """THE REGRESSION for defect 2.

    Measured 2026-08-20: the primary clone served a handoff 276 lines behind
    `origin/trunk` and the session was framed on it, caught only by luck. The
    digest must now name which copy it read, and read the authoritative one.
    """
    repo, _, v2 = make_stale_clone(tmp_path)
    out = run_resume(repo, stub_bin, extra_env={"RESUME_STATE_SKIP_FETCH": ""})
    line = read_line(out)
    assert "origin/main copy" in line, line
    assert "STALE" in line, line
    assert "9 lines local vs 285 on origin/main" in line, line
    # it is real drift, not a footnote
    joined = " ".join(drift_lines(out))
    assert "STALE" in joined, joined
    assert "matches the handoff's claims" not in joined, joined
    # 🔴 and the handed-over file must hold the ORIGIN text, not the stale one.
    # A first cut of this fix wrote the LOCAL copy there, so the digest said
    # "read this" while pointing at the very copy it had just called stale —
    # which would have re-created the bug through the instructions meant to fix
    # it. Asserting only that the path EXISTS passes that version.
    alt = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("handoff-other-copy:")]
    assert alt, out
    handed = Path(alt[0].split(":", 1)[1].strip())
    assert handed.exists(), out
    assert handed.read_text().rstrip("\n") == v2.rstrip("\n"), (
        "the file handed to the reader is not the origin text:\n"
        f"{handed.read_text()[:200]}"
    )


def test_the_origin_copy_is_what_later_blocks_actually_reconcile(tmp_path, stub_bin):
    """Not just reported — USED.

    Naming the stale copy while still extracting from it would be a comment,
    not a fix. The origin-only text carries a PR ref the working-tree copy does
    not, so the ONLY way that ref can be reconciled is if the origin copy is
    what got parsed.
    """
    _, log = stub_bin
    origin = tmp_path / "origin.git"
    repo, _, _ = make_stale_clone(tmp_path, origin_extra=3)
    # append a PR ref that exists ONLY on origin
    b = tmp_path / "cloneB"
    doc = "claudedocs/handoff-stale.md"
    (b / doc).write_text((b / doc).read_text() + "PR #9091 is OPEN and awaiting review.\n")
    for args in (["add", doc], ["commit", "-qm", "add pr ref"], ["push", "-q", "origin", "main"]):
        subprocess.run(["git", "-C", str(b), *args], check=True, env=_git_env(b))
    assert "9091" not in (repo / doc).read_text(), "fixture: ref must be origin-only"
    assert origin.exists()

    run_resume(
        repo,
        stub_bin,
        extra_env={"RESUME_STATE_SKIP_FETCH": "", "STUB_GH_JSON": MERGED_JSON},
    )
    # read defensively: at base the stale copy names NO PRs, so gh is never
    # invoked and the log does not exist at all — that must surface as this
    # assertion's message, not as a FileNotFoundError from the reader.
    calls = log.read_text() if log.exists() else ""
    assert any(" 9091 " in ln for ln in calls.splitlines()), (
        "the origin-only PR ref was never reconciled — the stale local copy was "
        f"parsed after all. gh calls seen:\n{calls or '(none — gh never ran)'}"
    )


def test_an_up_to_date_handoff_says_so_and_stays_clean(tmp_path, stub_bin):
    """POSITIVE CONTROL. The warning must be driven by an actual difference —
    a hardcoded 'STALE' would pass the test above."""
    repo, _, _ = make_stale_clone(tmp_path, origin_extra=0)
    out = run_resume(repo, stub_bin, extra_env={"RESUME_STATE_SKIP_FETCH": ""})
    line = read_line(out)
    assert "identical to origin/main" in line, line
    assert "STALE" not in line, line
    assert "matches the handoff's claims" in " ".join(drift_lines(out))


def test_uncommitted_local_edits_keep_the_local_copy_but_are_flagged(tmp_path, stub_bin):
    """The other direction, and it must NOT be called stale: a doc with
    uncommitted edits is this session's work-in-progress, so the local copy
    stays authoritative — but reconciling unpushed text is its own trap, so it
    is reported as a gap rather than passed over."""
    repo, _, _ = make_stale_clone(tmp_path, dirty=True)
    out = run_resume(repo, stub_bin, extra_env={"RESUME_STATE_SKIP_FETCH": ""})
    line = read_line(out)
    assert "working-tree copy" in line, line
    assert "UNCOMMITTED" in line, line
    assert "STALE" not in line, line
    joined = " ".join(drift_lines(out))
    assert "uncommitted local edits" in joined, joined
    assert "matches the handoff's claims" not in joined, joined


def test_freshness_is_reported_as_unchecked_rather_than_assumed(tmp_path, stub_bin):
    """No remote => the comparison could not be made. It must say UNCHECKED, not
    imply the working-tree copy was verified."""
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",))
    line = read_line(run_resume(repo, stub_bin))
    assert "UNCHECKED" in line, line
    assert "no origin remote" in line, line


def test_a_doc_absent_from_origin_is_not_reported_as_matching(tmp_path, stub_bin):
    """🔴 `git diff --quiet <ref> -- <path>` exits 0 when the path exists on
    NEITHER side, so a freshness check built on it alone reports a reassuring
    "matches origin/main" for a doc that has never been on that branch. This
    pins the cat-file existence probe that makes the comparison mean anything.
    """
    repo, _, _ = make_stale_clone(tmp_path, origin_extra=0)
    (repo / "claudedocs" / "handoff-brand-new.md").write_text(
        "# Handoff\nwritten this session, never pushed\n"
    )
    os.utime(repo / "claudedocs" / "handoff-brand-new.md", (1_800_000_000, 1_800_000_000))
    out = run_resume(repo, stub_bin, extra_env={"RESUME_STATE_SKIP_FETCH": ""})
    assert handoff_line(out) == "handoff: handoff-brand-new.md", out
    line = read_line(out)
    assert "identical to origin/main" not in line, line
    assert "untracked" in line or "not on origin/main" in line, line


# --------------------------------------------------------------------------- #
# 🔴 A PATH OUTSIDE ANY GIT REPO — the digest checked nothing and said so was
# fine
# --------------------------------------------------------------------------- #
def test_a_handoff_outside_any_git_repo_is_not_a_clean_bill_of_health(
    tmp_path, stub_bin
):
    """Hit live 2026-08-20 by passing an explicit handoff path outside a repo:
    the GIT/PR block returned at its not-a-git-repo guard, so no branch, PR or
    ahead/behind check ever ran — and DRIFT still printed
    "(none detected — live state matches the handoff's claims)".
    """
    loose = tmp_path / "loose"
    loose.mkdir()
    doc = loose / "handoff-orphan.md"
    doc.write_text("## Handoff\nPR #4101 is OPEN and awaiting review.\n")
    out = run_resume(tmp_path, stub_bin, str(doc), cwd=loose)
    assert "not a git repo" in out, out
    joined = " ".join(drift_lines(out))
    assert "matches the handoff's claims" not in joined, joined
    assert "no branch, ahead/behind or PR reconciliation ran" in joined, joined


# --------------------------------------------------------------------------- #
# 🔴 FABRICATED BRANCHES — the branch prefixes are also ordinary directory
# names, so a handoff that merely QUOTES A PATH used to mint a phantom branch
# and report it "no longer exists (merged & pruned?)"
# --------------------------------------------------------------------------- #
def test_a_quoted_file_path_is_not_reported_as_a_branch(tmp_path, stub_bin):
    """MEASURED LIVE in civitai-manager: its handoff backtick-quotes
    `docs/configuration.md`, a real 15 KB file, and the digest emitted
    `branch docs/configuration.md ... no longer exists (merged & pruned?)`.
    That line was in this PR's own body as success evidence."""
    repo = make_repo(
        tmp_path,
        docs=("SESSION-HANDOFF.md",),
        doc_body="## Handoff\n🔴 `docs/configuration.md` shipped a bad sample for ~89 releases.\n",
        files=("docs/configuration.md",),
    )
    out = run_resume(repo, stub_bin)
    assert branch_tokens(out) == [], f"fabricated a branch from a file path: {out}"
    assert "docs/configuration.md" not in " ".join(drift_lines(out)), drift_lines(out)


def test_a_quoted_file_path_that_is_not_tracked_here_is_not_a_branch(tmp_path, stub_bin):
    """The DISCRIMINATING half of the test above, which is over-determined.

    There the file really is tracked, so the `git cat-file` probe catches it and
    the extension filter is never load-bearing — deleting that filter left the
    whole suite GREEN. Here the quoted path does not exist in this repo at all
    (handoffs routinely cite paths in OTHER repos, or files since deleted), so
    only the extension filter can reject it. Keep both cases: one is the real
    measured shape, this one is the one that can fail.
    """
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nSee `docs/upstream-notes.md` in the other repo, and fix/real-branch.\n",
    )
    out = run_resume(repo, stub_bin)
    toks = branch_tokens(out)
    assert "docs/upstream-notes.md" not in toks, f"fabricated a branch from an untracked path: {toks}"
    # and the genuine reference beside it still survives, so this is not just
    # "nothing was extracted"
    assert toks == ["fix/real-branch"], toks


def test_an_absolute_path_containing_a_prefix_is_not_a_branch(tmp_path, stub_bin):
    """MEASURED LIVE in naida-ai: the prose "local checkout
    `/home/zach/workspace/scratch/naida-ai`" yielded a branch token
    `zach/workspace/scratch/naida-ai`, because `\\b` matches after a slash."""
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nlocal checkout `/home/zach/workspace/scratch/naida-ai` is stale.\n",
    )
    out = run_resume(repo, stub_bin)
    assert branch_tokens(out) == [], f"fabricated a branch from an absolute path: {out}"


def test_a_quoted_tracked_directory_is_not_a_branch(tmp_path, stub_bin):
    """The extensionless case the string filters cannot see — caught by the
    `git cat-file -e HEAD:<tok>` probe in the branch loop."""
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nEverything under `docs/architecture` needs a rewrite.\n",
        files=("docs/architecture/overview.md",),
    )
    assert branch_tokens(run_resume(repo, stub_bin)) == []


def test_a_genuinely_missing_branch_is_still_reported(tmp_path, stub_bin):
    """POSITIVE CONTROL for the three tests above.

    They assert an ABSENCE, which is exactly what deleting extract_branches
    entirely would also produce. This proves the branch machinery still works:
    a real branch reference that no longer exists must still surface, both as a
    branch line and as DRIFT.
    """
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nWork continues on feat/vanished-branch.\n",
    )
    out = run_resume(repo, stub_bin)
    assert branch_tokens(out) == ["feat/vanished-branch"], out
    assert any("feat/vanished-branch" in ln for ln in drift_lines(out)), drift_lines(out)


def test_an_existing_branch_is_reported_as_existing_not_gone(tmp_path, stub_bin):
    """Second positive control: the loop distinguishes present from absent, so
    the filters above are not simply suppressing every branch."""
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nWork continues on feat/live-branch.\n",
        branches=("feat/live-branch",),
    )
    out = run_resume(repo, stub_bin)
    assert branch_tokens(out) == ["feat/live-branch"], out
    assert not any("feat/live-branch" in ln for ln in drift_lines(out)), drift_lines(out)


def test_the_word_boundary_behaviour_is_preserved(tmp_path, stub_bin):
    """`-` and `.` must still delimit as `\\b` did, and an embedded prefix must
    still NOT match.

    ⚠ This test is documented as guarding against "silently dropping real
    branch references", and for one round it did NOT: it exercises only `-` and
    `notafix/`, so it structurally could not see the `origin/` class below. The
    parametrised test that follows is the part that actually covers that claim.
    """
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nsee my-fix/kept-branch, and notafix/must-not-match.\n",
    )
    toks = branch_tokens(run_resume(repo, stub_bin))
    assert "fix/kept-branch" in toks, toks
    assert not any("must-not-match" in t for t in toks), toks


# Ref-ish spellings that carry a slash-bearing prefix. Excluding `/` from the
# leading boundary killed these — a real regression, measured across 211 real
# handoff docs: `origin/zach/engaged-models-client-store` in datapacket-talos is
# the ONLY form that branch appears in, and it is genuinely gone, so the
# pre-regression DRIFT line was correct and the regressed code was silently
# mute. Omission replacing fabrication is the same disease in a go/no-go tool —
# it reads as "no drift".
#
# 🔴 REGRESSION COVERAGE vs INVARIANT GUARD — and WHICH regression. The third
# tuple element names the prior script version a row actually goes RED against;
# a row that goes red against nothing is an invariant guard and the repo's rules
# say it must not be counted as regression coverage. All of it MEASURED by
# replaying each spelling through each old version, not asserted:
#
#   version    what it is
#   base       pre-PR: `\b` boundary, no sed pre-strip
#   r2         this PR round 2: `/`-excluding boundary, no sed  (the F3 regression)
#   r3         this PR round 3 (dfcb800): sed strips tree AND compare
#
#   row                                     base   r2    r3     -> pins
#   origin/fix/wanted                       same   RED   same      r2
#   upstream/feat/wanted                    same   RED   same      r2
#   refs/heads/fix/wanted                   same   RED   same      r2
#   refs/remotes/origin/zach/wanted         same   RED   same      r2
#   .../tree/feat/wanted                    same   RED   same      r2
#   .../compare/main...feat/wanted          same   same  same      NOTHING
#   .../compare/zach/a...zach/b             RED    same  RED       base + r3
#
# ⚠ Note what that first column says: against the PRE-PR base every one of the
# first six rows is an INVARIANT — base's `\b` already handled all of them. The
# defect these rows exist for is round 2's own regression, not the original bug.
# Saying "regression coverage" without naming the reference point is how a table
# like this reads as more than it is.
SLASHED_REF_FORMS = [
    # (spelling, expected, pins)
    ("origin/fix/wanted", "fix/wanted", "r2"),
    ("upstream/feat/wanted", "feat/wanted", "r2"),
    ("refs/heads/fix/wanted", "fix/wanted", "r2"),
    ("refs/remotes/origin/zach/wanted", "zach/wanted", "r2"),
    ("https://github.com/a/b/tree/feat/wanted", "feat/wanted", "r2"),
    # Pins NOTHING — kept as a documented invariant, not deleted, because it is
    # the shape a reader assumes is covered. It was previously described as
    # regression coverage; that description measured FALSE against every prior
    # version.
    ("https://github.com/a/b/compare/main...feat/wanted", "feat/wanted", "nothing"),
    # Pins the decision NOT to strip `/compare/`. r3 stripped it and produced the
    # junk token `zach/a...zach/b`; base produced the same junk. Not stripping
    # yields `zach/b`, the head of the compare, which is the ref a reader means —
    # so this row guards an improvement over BOTH. Re-adding `|compare` -> RED.
    ("https://github.com/a/b/compare/zach/a...zach/b", "zach/b", "base+r3"),
]


@pytest.mark.parametrize("spelling,expected,pins", SLASHED_REF_FORMS)
def test_a_ref_with_a_slashed_prefix_is_still_extracted(
    tmp_path, stub_bin, spelling, expected, pins
):
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body=f"## Handoff\nRebase onto {spelling} before continuing.\n",
    )
    toks = branch_tokens(run_resume(repo, stub_bin))
    assert toks == [expected], f"{spelling!r} -> {toks}, expected [{expected!r}]"


def test_the_slashed_ref_table_keeps_its_real_coverage():
    """Meta-guard on the table above — pin the SHAPE, not just the rows.

    The table mixes rows that pin a real prior defect with rows that pin
    nothing, and that split is only useful while it stays honest. Deleting the
    rows that actually go red, or relabelling them, would leave a table that
    still looks like a thorough sweep while covering nothing. This is the guard
    that notices.
    """
    pins = [p for _, _, p in SLASHED_REF_FORMS]
    r2 = [s for s, _, p in SLASHED_REF_FORMS if p == "r2"]
    assert len(r2) >= 5, f"r2 regression rows dropped to {len(r2)}: {r2}"
    # the shape of the one live casualty measured in the field must be covered
    assert any(s.startswith("origin/") for s in r2), r2
    # and the /compare/ no-strip decision must stay pinned by something
    assert "base+r3" in pins, pins
    # every label must be one we have actually measured
    assert set(pins) <= {"r2", "base+r3", "nothing"}, set(pins)


def test_a_remote_like_path_segment_is_not_stripped(tmp_path, stub_bin):
    """The `origin|upstream` rule's leading bound is load-bearing — but NOT for
    the case the obvious fixture uses, which is why this needed measuring.

    `/home/zach/repos/origin/fix/x` does NOT discriminate: with the bound
    loosened, `origin/` is stripped and the result is `/home/zach/repos/fix/x`,
    where `fix` is still preceded by `/` and the grep boundary rejects it
    anyway. Both versions yield nothing, so a test built on it passes with the
    bound deleted — measured, and the first version of this test did exactly
    that.

    The discriminating shape is a remote-like segment glued to a WORD, inside a
    path. Measured with the bound loosened to `s#(origin|upstream)/##g`:

        /home/zach/repos/origin/fix/x  -> []        (same, cannot discriminate)
        /var/log/my-origin/fix/x       -> [fix/x]   <- FABRICATED from a path
        .origin/fix/x                  -> [fix/x]
        my-origin/fix/x                -> [fix/x]

    Accepted cost of the bound: a genuinely remote-qualified `my-origin/fix/x`
    yields nothing. That is an omission, and the fabrication is the worse
    direction, so the bound stays.
    """
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nBuilt from `/var/log/my-origin/fix/x` last week.\n",
    )
    assert branch_tokens(run_resume(repo, stub_bin)) == []


def test_stripping_ref_prefixes_does_not_revive_path_fabrication(tmp_path, stub_bin):
    """The two fixes pull in opposite directions, so pin them TOGETHER.

    Stripping `origin/`-style prefixes must not re-open the door that filter 1
    closed: a bare filesystem path carries no ref prefix and must still yield
    nothing, in the same document that legitimately names a prefixed ref.
    """
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body=(
            "## Handoff\n"
            "local checkout `/home/zach/workspace/scratch/naida-ai` is stale.\n"
            "`docs/configuration.md` shipped a bad sample.\n"
            "Rebase onto origin/zach/engaged-models-client-store first.\n"
        ),
    )
    assert branch_tokens(run_resume(repo, stub_bin)) == [
        "zach/engaged-models-client-store"
    ]


def test_a_token_that_is_both_a_live_branch_and_a_tracked_path_is_a_branch(
    tmp_path, stub_bin
):
    """The tracked-path probe must not outrank a real branch.

    It used to run FIRST, so a token that was both silently vanished. No such
    collision exists across 2893 real branches, so this is a latent wrong-drop
    rather than a live bug — but the ordering is cheap to get right and cheaper
    to pin than to rediscover.
    """
    repo = make_repo(
        tmp_path,
        docs=("HANDOFF.md",),
        doc_body="## Handoff\nWork continues on docs/overhaul.\n",
        files=("docs/overhaul",),      # a tracked FILE of exactly that name
        branches=("docs/overhaul",),   # …and a real branch of exactly that name
    )
    out = run_resume(repo, stub_bin)
    assert branch_tokens(out) == ["docs/overhaul"], out
    assert not any("docs/overhaul" in ln for ln in drift_lines(out)), drift_lines(out)


# --------------------------------------------------------------------------- #
# hermeticity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool", ["gh", "kubectl", "curl"])
def test_stub_tripwire_actually_fires_when_the_tool_is_run(tmp_path, stub_bin, tool):
    """POSITIVE CONTROL for the tripwire below.

    That test asserts a ZERO — an empty invocation log — and a zero is
    indistinguishable from a stub that cannot exec at all. A stub whose shebang
    is wrong (the exact defect testlib.mockbin exists to prevent) writes nothing
    and would make the tripwire green forever. So drive each stub directly and
    watch the number move: 1 here, 0 under test.
    """
    d, log = stub_bin
    if log.exists():
        log.unlink()
    env = dict(os.environ)
    env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    out = subprocess.run(
        [tool, "--probe"], env=env, capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 1, f"{tool} stub did not exec: rc={out.returncode} {out.stderr}"
    assert log.exists(), f"{tool} stub ran but logged nothing"
    assert log.read_text().strip() == f"{tool} --probe"
    log.unlink()


def test_no_network_tool_is_ever_invoked(tmp_path, stub_bin):
    """Tripwire. If a fixture ever grows a remote or a prod-kubeconfig, the
    script reaches for gh/kubectl/curl and this suite starts costing seconds
    (or hangs on a real timeout) — fail here instead, with the command named.
    """
    _, log = stub_bin
    if log.exists():
        log.unlink()
    for docs in ((), ("SESSION-HANDOFF.md",), ("handoff-foo.md",), DECOY_DOCS):
        repo = make_repo(tmp_path, docs=docs, name=f"r{len(docs)}{docs[:1]}")
        run_resume(repo, stub_bin)
    assert not log.exists() or log.read_text() == "", (
        f"a network tool was invoked:\n{log.read_text()}"
    )


# --------------------------------------------------------------------------- #
# A RELATIVE token anchored one level above the repo.
#
# `/handoff`'s kickoff template emits `<repo>/claudedocs/handoff-<topic>.md`.
# That resolves from the repo's PARENT and NOT from the repo, which is where a
# kickoff is actually pasted. MEASURED 2026-08-30: the miss sent the run down
# the newest-of-N fallback and it reconciled a DIFFERENT INITIATIVE.
#
# 🔴 EVERY fixture below carries a NEWER DECOY. Without one the fallback would
# select the same file the token names, and each test would pass whether or not
# the re-anchoring clause exists — the vacuous-green shape this suite exists to
# avoid. `docs[-1]` is the newest, so the decoy goes last.
# --------------------------------------------------------------------------- #

WANTED = "handoff-wanted.md"
DECOY = "handoff-zz-newer-decoy.md"


def test_a_repo_prefixed_token_resolves_from_INSIDE_the_repo(tmp_path, stub_bin):
    """The exact shape the kickoff template emits, pasted where it is pasted."""
    repo = make_repo(tmp_path, docs=(WANTED, DECOY), name="devrc")
    out = run_resume(repo, stub_bin, f"devrc/claudedocs/{WANTED}", cwd=repo)
    assert handoff_line(out) == f"handoff: {WANTED}", out
    assert gap_lines(out) == [], out


def test_the_decoy_PROVES_the_test_above_is_not_vacuous(tmp_path, stub_bin):
    """POSITIVE CONTROL on the fixture, not on the subject.

    If the fallback would have picked WANTED anyway, the test above proves
    nothing about the re-anchoring. Run with NO argument: the fallback must
    choose the DECOY. That is what makes `handoff: WANTED` above evidence.
    """
    repo = make_repo(tmp_path, docs=(WANTED, DECOY), name="devrc")
    assert handoff_line(run_resume(repo, stub_bin)) == f"handoff: {DECOY}"


def test_a_bare_claudedocs_token_resolves_from_a_SUBDIRECTORY(tmp_path, stub_bin):
    """Same clause, second shape: `claudedocs/handoff-x.md` typed from a subdir."""
    repo = make_repo(tmp_path, docs=(WANTED, DECOY), name="devrc")
    sub = repo / "scripts"
    sub.mkdir(exist_ok=True)
    out = run_resume(repo, stub_bin, f"claudedocs/{WANTED}", cwd=sub)
    assert handoff_line(out) == f"handoff: {WANTED}", out
    assert gap_lines(out) == [], out


def test_an_ABSOLUTE_token_that_is_absent_STAYS_a_gap(tmp_path, stub_bin):
    """🔴 THE LOAD-BEARING RESTRICTION, and the reason the clause is safe.

    An absolute path names a SPECIFIC tree. Re-anchoring it on this repo would
    serve a same-named doc from the wrong checkout — the very wrong-initiative
    failure the clause removes, reintroduced one level down and harder to see.
    A same-named doc EXISTS here, so a clause that re-anchored absolutes would
    resolve it and this test would fail.
    """
    repo = make_repo(tmp_path, docs=(WANTED, DECOY), name="devrc")
    absent = f"/nonexistent-checkout/claudedocs/{WANTED}"
    out = run_resume(repo, stub_bin, absent, cwd=repo)
    assert any(absent in g for g in gap_lines(out)), out
    assert handoff_line(out) != f"handoff: {WANTED}", out


def test_a_repo_prefixed_token_naming_a_doc_THIS_repo_lacks_stays_a_gap(
    tmp_path, stub_bin
):
    """Re-anchoring must not invent a resolution. Relative, but absent here."""
    repo = make_repo(tmp_path, docs=(WANTED, DECOY), name="devrc")
    tok = "devrc/claudedocs/handoff-never-existed.md"
    out = run_resume(repo, stub_bin, tok, cwd=repo)
    assert any(tok in g for g in gap_lines(out)), out


def test_a_token_under_a_NON_claudedocs_dir_is_still_ignored(tmp_path, stub_bin):
    """The dir-shape test still runs FIRST; re-anchoring did not widen it."""
    repo = make_repo(tmp_path, docs=(WANTED, DECOY), name="devrc")
    out = run_resume(repo, stub_bin, f"devrc/notdocs/{WANTED}", cwd=repo)
    assert handoff_line(out) == f"handoff: {DECOY}", out


def test_the_FIRST_resolvable_token_wins_not_the_last(tmp_path, stub_bin):
    """🔴 Kills the `break`-removed mutant, which survived the first sweep.

    Without the `break` the loop keeps going and a LATER token overwrites the
    hit, silently making the function last-wins. Its own docstring and its
    `miss` bookkeeping both promise FIRST. One token is not enough to tell the
    two apart -- this needs two, BOTH resolvable, and they must be distinct
    files or the assertion cannot see the difference.
    """
    first, second = "handoff-first-named.md", "handoff-second-named.md"
    repo = make_repo(tmp_path, docs=(first, second, DECOY), name="devrc")
    out = run_resume(
        repo, stub_bin,
        f"devrc/claudedocs/{first} and also devrc/claudedocs/{second}",
        cwd=repo,
    )
    assert handoff_line(out) == f"handoff: {first}", out


# --------------------------------------------------------------------------- #
# 🔴 #1164 — A HANDOFF THAT LIVES IN A LINKED WORKTREE OF THE CLONE THAT WAS
# NAMED
#
# `claude/RULES.md` makes worktree isolation the standing default for any agent
# that modifies files, so handoff docs land in linked worktrees BY
# CONSTRUCTION — while `/handoff`'s own kickoff template, and any human writing
# the path by hand, names the BASE CLONE. MEASURED 2026-08-31 against the #1159
# fix: `NO SUCH FILE`, a fall back to newest-of-91, and a complete confident
# digest — PR states, a CLAWGATE block, DRIFT findings — about a DIFFERENT
# initiative.
#
# Two independent changes are pinned below and they must not be conflated:
#   (1) the named clone's worktrees are searched, scoped to THAT clone;
#   (2) a named-but-missing handoff no longer falls back AT ALL.
# (2) alone would leave the operator with nothing; (1) alone would leave the
# wrong-initiative digest in place whenever the doc is genuinely gone.
#
# 🔴 EVERY FIXTURE HERE CARRIES A NEWER DECOY IN THE BASE CLONE. Without one the
# fallback would have nothing to pick and the assertions would pass whether or
# not either half exists — the vacuous-green shape this module keeps catching.
# `test_the_base_clone_decoy_PROVES_these_are_not_vacuous` is the control.
# --------------------------------------------------------------------------- #

IN_WORKTREE = "handoff-in-a-worktree-2026-08-31.md"
BASE_DECOY = "handoff-zz-base-clone-decoy.md"


def add_worktree(repo, name, branch, docs=()):
    """A REAL linked worktree of `repo`, on its own branch, holding `docs`.

    Not a simulation: `git worktree add` is what creates the `.git`-file
    checkout that `git worktree list --porcelain` enumerates, and the resolution
    under test reads that command's output. A fixture that merely made a sibling
    directory would pass against an implementation that globbed `../*` and prove
    nothing about the clone-scoping guarantee.

    The docs are COMMITTED on `branch` and exist nowhere else, which is the
    shape the incident had: the base clone has never held the file.
    """
    wt = repo.parent / name
    env = _git_env(repo)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", branch, str(wt)],
        check=True, env=env, capture_output=True,
    )
    (wt / "claudedocs").mkdir(parents=True, exist_ok=True)
    rels = []
    for i, doc in enumerate(docs):
        p = wt / "claudedocs" / doc
        p.write_text(f"## {doc}\nsome handoff prose\n")
        os.utime(p, (1_700_000_000 + i * 1000, 1_700_000_000 + i * 1000))
        rels.append(f"claudedocs/{doc}")
    if rels:
        subprocess.run(["git", "-C", str(wt), "add", *rels], check=True, env=env)
        subprocess.run(
            ["git", "-C", str(wt), "commit", "-qm", f"handoff on {branch}"],
            check=True, env=env,
        )
    return wt


def test_a_handoff_in_a_LINKED_WORKTREE_resolves_from_the_BASE_CLONE_path(
    tmp_path, stub_bin
):
    """🔴 THE REGRESSION for #1164, built from a real `git worktree add`.

    RED at the pre-fix sha: the base-clone path misses, the run falls back, and
    `handoff:` names BASE_DECOY — a different initiative.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    wt = add_worktree(repo, "devrc-topic", "feat/topic", docs=(IN_WORKTREE,))
    named = repo / "claudedocs" / IN_WORKTREE
    assert not named.exists(), "the fixture must NOT put the doc in the base clone"
    assert (wt / "claudedocs" / IN_WORKTREE).is_file()

    out = run_resume(repo, stub_bin, str(named), cwd=repo)
    assert handoff_line(out) == f"handoff: {IN_WORKTREE}", out
    assert gap_lines(out) == [], out
    # …and the digest follows the doc into ITS checkout, as it does for any
    # other resolved path — otherwise the GIT/PR block reconciles the base
    # clone's branch against a worktree's handoff.
    repo_line = [ln for ln in out.splitlines() if ln.startswith("# repo:")][0]
    assert "devrc-topic" in repo_line, repo_line


def test_the_base_clone_decoy_PROVES_these_are_not_vacuous(tmp_path, stub_bin):
    """POSITIVE CONTROL on the fixture, not on the subject.

    If the base clone's newest doc were the one the tests above want, they would
    pass with the worktree search deleted. Run with NO argument: the fallback
    must choose BASE_DECOY. That is what makes `handoff: IN_WORKTREE` evidence.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    add_worktree(repo, "devrc-topic", "feat/topic", docs=(IN_WORKTREE,))
    assert handoff_line(run_resume(repo, stub_bin)) == f"handoff: {BASE_DECOY}"


def test_an_ABSENT_clone_is_not_served_out_of_THIS_repos_worktrees(
    tmp_path, stub_bin
):
    """🔴 THE WRONG-INITIATIVE INVARIANT, in the shape the fix could break it.

    The caller named `/nonexistent-checkout/...`. A same-named doc IS reachable
    from the cwd's own clone — it sits in a linked worktree right there — so an
    implementation that enumerated `$PWD`'s worktrees instead of the NAMED
    clone's resolves it and this fails. That is the very bug `embedded_md_path`'s
    absolute-token restriction exists to remove, reintroduced one level down.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    add_worktree(repo, "devrc-topic", "feat/topic", docs=(IN_WORKTREE,))
    tok = f"/nonexistent-checkout/claudedocs/{IN_WORKTREE}"
    out = run_resume(repo, stub_bin, tok, cwd=repo)
    assert handoff_line(out) == "handoff: (none found — git-only)", out
    assert any(tok in g for g in gap_lines(out)), out


def test_an_UNRELATED_repos_worktrees_are_never_searched(tmp_path, stub_bin):
    """🔴 The same invariant with a clone that EXISTS — and its own control.

    `other-repo` holds the doc in a linked worktree. Naming `devrc`'s path for
    the same basename must find nothing, because devrc's clone does not have it
    anywhere. The second half runs the SAME basename against `other-repo`'s path
    and requires it to resolve: without that, a search wired to nothing would
    satisfy the first half for free.
    """
    other = make_repo(tmp_path, docs=(), name="other-repo")
    add_worktree(other, "other-repo-wt", "feat/other", docs=(IN_WORKTREE,))
    here = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    add_worktree(here, "devrc-empty", "feat/empty")

    miss = run_resume(here, stub_bin, str(here / "claudedocs" / IN_WORKTREE), cwd=here)
    assert handoff_line(miss) == "handoff: (none found — git-only)", miss

    # POSITIVE CONTROL: the same basename, named against the clone that has it.
    hit = run_resume(here, stub_bin, str(other / "claudedocs" / IN_WORKTREE), cwd=here)
    assert handoff_line(hit) == f"handoff: {IN_WORKTREE}", hit


def test_TWO_worktrees_holding_the_basename_pick_NOTHING_and_say_so(
    tmp_path, stub_bin
):
    """🔴 AMBIGUITY IS REFUSED, NOT BROKEN.

    Two worktrees of one clone holding the same handoff basename are two
    revisions of it. Picking by list order or mtime would put the whole digest
    on a coin flip, silently — the newest-of-N failure this module exists for,
    one level in. Nothing is chosen, and the gap says how many were found and
    where.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    a = add_worktree(repo, "devrc-a", "feat/a", docs=(IN_WORKTREE,))
    b = add_worktree(repo, "devrc-b", "feat/b", docs=(IN_WORKTREE,))
    tok = repo / "claudedocs" / IN_WORKTREE

    out = run_resume(repo, stub_bin, str(tok), cwd=repo)
    assert handoff_line(out) == "handoff: (none found — git-only)", out
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert f"exists in 2 worktrees" in gaps[0], gaps
    for wt in (a, b):
        assert f"{wt.name}/claudedocs/{IN_WORKTREE}" in gaps[0], gaps
    # …and the sentence is pinned WHOLE, like every other shape this block emits.
    # The expected paths are the ones the FIXTURE handed `git worktree add`, not
    # anything parsed back out of the digest — an expectation read off the
    # subject cannot fail. The shell sorts them, so this does too.
    paths = ", ".join(sorted(f"{w}/claudedocs/{IN_WORKTREE}" for w in (a, b)))
    assert gaps[0] == GAP_LEAD_AMBIGUOUS.format(
        tok=tok, base=IN_WORKTREE, n=2, paths=paths
    ) + GAP_REST_NONE, gaps[0]


@pytest.mark.parametrize(
    "shape",
    [
        # the shape `/handoff`'s kickoff template emits, pasted inside the repo
        pytest.param("devrc/claudedocs/{doc}", id="repo-prefixed"),
        # the shape a human types from a subdirectory
        pytest.param("claudedocs/{doc}", id="bare-claudedocs"),
    ],
)
def test_a_RELATIVE_token_also_reaches_the_clones_worktrees(
    tmp_path, stub_bin, shape
):
    """🔴 THE SECOND SEARCH SITE, and nothing else here can see it.

    A relative token is re-anchored on `$root` — the cwd's own repo root — which
    is why the absolute-token restriction does not apply to it. Neither shape
    below reaches the named-clone branch: `devrc/` resolves to nothing from
    inside the repo, and `claudedocs/` has no directory prefix at all. So if the
    `$root` anchor is not ALSO widened to that clone's worktrees, both miss, and
    the kickoff template — which emits the first shape — keeps failing exactly
    as #1164 describes.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    add_worktree(repo, "devrc-topic", "feat/topic", docs=(IN_WORKTREE,))
    out = run_resume(repo, stub_bin, shape.format(doc=IN_WORKTREE), cwd=repo)
    assert handoff_line(out) == f"handoff: {IN_WORKTREE}", out
    assert gap_lines(out) == [], out


@pytest.mark.parametrize(
    "n_wt,more",
    [
        pytest.param(4, "", id="at-the-cap"),
        pytest.param(5, ", and 1 more", id="over-the-cap"),
    ],
)
def test_the_ambiguous_ENUMERATION_is_capped_but_the_COUNT_is_not(
    tmp_path, stub_bin, n_wt, more
):
    """🔴 MEASURED, not hypothetical: this host's devrc clone has 142 linked
    worktrees and one handoff basename present in 28 of them. An uncapped list
    is a ~2.5 KB single line inside the block whose whole job is to be read.

    Both sides of the threshold, because a rule that depends on a count is only
    pinned if both sides of it are — and the COUNT must keep naming the real
    total on the capped side, or the cap has quietly shrunk the finding.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    wts = [
        add_worktree(repo, f"devrc-w{i}", f"feat/w{i}", docs=(IN_WORKTREE,))
        for i in range(n_wt)
    ]
    tok = repo / "claudedocs" / IN_WORKTREE
    gaps = gap_lines(run_resume(repo, stub_bin, str(tok), cwd=repo))
    assert len(gaps) == 1, gaps
    paths = sorted(f"{w}/claudedocs/{IN_WORKTREE}" for w in wts)
    assert gaps[0] == GAP_LEAD_AMBIGUOUS.format(
        tok=tok, base=IN_WORKTREE, n=n_wt, paths=", ".join(paths[:4]) + more
    ) + GAP_REST_NONE, gaps[0]


def test_ONE_worktree_holding_the_basename_is_not_ambiguous(tmp_path, stub_bin):
    """THE OTHER SIDE OF THE BOUNDARY — a count-dependent rule needs both.

    With one hit the run resolves and emits NO gap; the ambiguity sentence must
    not appear. Measured at 1 and at 2 so a threshold that slid either way is
    visible.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    add_worktree(repo, "devrc-a", "feat/a", docs=(IN_WORKTREE,))
    add_worktree(repo, "devrc-b", "feat/b")            # a worktree WITHOUT the doc
    out = run_resume(repo, stub_bin, str(repo / "claudedocs" / IN_WORKTREE), cwd=repo)
    assert handoff_line(out) == f"handoff: {IN_WORKTREE}", out
    assert gap_lines(out) == [], out


# --------------------------------------------------------------------------- #
# 🔴 #1164 part 2 — A NAMED-MISSING HANDOFF RECONCILES NOTHING
#
# `named_missing` was recorded and then ignored: the newest-of-N chain ran
# anyway. The gap line was honest; the digest printed under it was a complete
# reconciliation of an unrelated initiative, and DRIFT — which the /resume skill
# tells the reader to read — carried findings about the wrong work.
#
# Deliberately NOT an exit code and NOT a refusal: the script has never had one
# and every caller would have to learn it. An empty HANDOFF routes to the
# existing, already-tested "NOTHING was reconciled" branch.
# --------------------------------------------------------------------------- #
def test_a_named_missing_handoff_reconciles_NONE_of_the_docs_present(
    tmp_path, stub_bin
):
    """THE REGRESSION for part 2. RED at the pre-fix sha: `handoff: {NEWEST}`.

    Three real handoffs sit in claudedocs/ — the run had plenty to substitute
    and must substitute none of them. Asserted over the WHOLE digest, not just
    the `handoff:` line, because the harm was the blocks underneath it.
    """
    docs = ("handoff-alpha-2026-01-01.md", "handoff-beta-2026-02-02.md", NEWEST)
    repo = make_repo(tmp_path, docs=docs)
    tok = repo / "claudedocs" / "handoff-never-existed-2026-09-01.md"
    out = run_resume(repo, stub_bin, str(tok), cwd=repo)

    assert handoff_line(out) == "handoff: (none found — git-only)", out
    for doc in docs:
        assert doc not in out, f"{doc} was reconciled anyway\n{out}"
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert gaps[0].endswith(GAP_REST_NONE), gaps
    assert "FELL BACK" not in gaps[0], gaps
    assert "matches the handoff's claims" not in " ".join(drift_lines(out)), out


def test_the_three_docs_ARE_takeable_which_is_what_makes_the_test_above_mean_it(
    tmp_path, stub_bin
):
    """POSITIVE CONTROL for the fixture above. With no argument the fallback
    takes NEWEST, so `doc not in out` is a fact about the guard, not about a
    repo that had nothing to offer."""
    docs = ("handoff-alpha-2026-01-01.md", "handoff-beta-2026-02-02.md", NEWEST)
    repo = make_repo(tmp_path, docs=docs)
    assert handoff_line(run_resume(repo, stub_bin)) == f"handoff: {NEWEST}"


def test_a_bare_BASENAME_slug_STILL_falls_back_and_resolves(tmp_path, stub_bin):
    """🔴 THE SCOPING GUARD — part 2 applies to `named_missing`, NOT `unresolved`.

    `resume-state.sh handoff-alpha-2026-01-01.md` is what a user pastes, and
    resume-state.sh's own comment records it as MEASURED: a bare basename is a
    SLUG, not a handoff-shaped path, so it never sets `named_missing`, and the
    fallback serves precisely the doc the reader wanted. Widening the no-fallback
    guard to `unresolved` breaks this — which is a regression, not a fix.
    """
    doc = "handoff-alpha-2026-01-01.md"
    repo = make_repo(tmp_path, docs=(doc,))
    out = run_resume(repo, stub_bin, doc, cwd=repo)
    assert handoff_line(out) == f"handoff: {doc}", out
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert "FELL BACK" in gaps[0], gaps


def test_the_civitai_slug_STILL_falls_back_and_resolves(tmp_path, stub_bin):
    """The second measured invocation the scoping guard protects: `session` in a
    repo whose only doc IS SESSION-HANDOFF.md, blessed by name in resolve()."""
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",))
    out = run_resume(repo, stub_bin, "session", cwd=repo)
    assert handoff_line(out) == "handoff: SESSION-HANDOFF.md", out


# --------------------------------------------------------------------------- #
# 🔴 #1197 AUDIT ROUND 1, F1 — A RELATIVE TOKEN NAMING A *FOREIGN* TREE WAS
# SERVED OUT OF THE STANDING CLONE'S WORKTREES, SILENTLY.
#
# The absolute/`<X>/claudedocs/` branch is scoped to `${dir%/claudedocs}` — the
# tree the caller named. The RELATIVE branch was not: it re-anchored on `$root`
# (the cwd's repo) and, since #1164, searched `$root`'s WORKTREES too, for ANY
# `<Y>`. So `other-repo/claudedocs/<base>` typed inside `devrc` resolved a copy
# out of `devrc`'s linked worktree, with NO gap.
#
# MEASURED 2026-09-01 in /tmp/f1v (devrc + linked worktree devrc-topic holding
# the doc, sibling other-repo that does not):
#
#   d86e5f81  handoff: (none found — git-only) + the NO SUCH FILE gap  (honest)
#   3e42bb04  handoff: handoff-only-in-worktree.md, # repo: …/devrc-topic,
#             gaps EMPTY                                              (silent, wrong clone)
#
# The discriminator is the NAME, not resolvability: `other-repo` is a SIBLING of
# the repo, so it does not resolve from the cwd at all and a `[ -d ]` test falls
# straight through to `$root`. `$root` may be used only when <Y> is empty, or
# <Y>'s LAST component is the cwd repo's own directory name.
#
# 🔴 EVERY FIXTURE BELOW CARRIES THE DOC IN A LINKED WORKTREE AND A NEWER DECOY
# IN THE BASE CLONE, so "it missed" cannot be satisfied by a repo that had
# nothing to serve — the shapes that MUST still resolve are asserted on the very
# same fixture, in the same test.
# --------------------------------------------------------------------------- #
FOREIGN_SHAPES = [
    # the reproduction: a sibling repo that EXISTS and does not hold the doc
    pytest.param("other-repo", True, id="foreign-sibling-repo-exists"),
    # …and one that is not on disk at all, which the same rule must cover
    pytest.param("no-such-tree", False, id="foreign-name-does-not-exist"),
]


@pytest.mark.parametrize("ydir,make_it", FOREIGN_SHAPES)
def test_a_FOREIGN_relative_token_is_not_served_from_THIS_clones_worktrees(
    tmp_path, stub_bin, ydir, make_it
):
    """🔴 THE REGRESSION for F1. RED at 3e42bb04: resolves, no gap.

    The doc exists in `devrc`'s own linked worktree, so a run that searches
    `$root`'s clone WILL find it — that is what makes this test able to fail.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    wt = add_worktree(repo, "devrc-topic", "feat/topic", docs=(IN_WORKTREE,))
    assert (wt / "claudedocs" / IN_WORKTREE).is_file()
    if make_it:
        make_repo(tmp_path, docs=(), name=ydir)

    out = run_resume(repo, stub_bin, f"{ydir}/claudedocs/{IN_WORKTREE}", cwd=repo)
    assert handoff_line(out) == "handoff: (none found — git-only)", out
    assert IN_WORKTREE not in out.replace(f"{ydir}/claudedocs/{IN_WORKTREE}", ""), out
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert f"{ydir}/claudedocs/{IN_WORKTREE}" in gaps[0], gaps
    assert gaps[0].endswith(GAP_REST_NONE), gaps


@pytest.mark.parametrize("ydir,make_it", FOREIGN_SHAPES)
def test_a_FOREIGN_relative_token_is_not_re_anchored_on_THIS_repos_own_copy(
    tmp_path, stub_bin, ydir, make_it
):
    """🔴 THE SINGLE-TREE HALF OF THE SAME HOLE — no worktrees involved at all.

    #1159's re-anchor (`$root/claudedocs/$base`) fired for ANY `<Y>`, so this
    predates the worktree search entirely: `other-repo/claudedocs/<base>` typed
    inside `devrc` resolved DEVRC's own copy, silently. F1's `$mine` gate closes
    it as a side effect, and NOTHING SAW IT until the mutation battery: X1 (drop
    the gate from the re-anchor, keep it on the worktree search) SURVIVED all
    178 tests, because every foreign-token fixture kept the doc in a WORKTREE
    and the base clone therefore had nothing for the re-anchor to find. This
    fixture puts the doc in the base clone instead — the only shape that can
    tell the two clauses apart.

    The positive control is `test_a_repo_prefixed_token_resolves_from_INSIDE_the
    _repo` above: same fixture, same doc, `<Y>` swapped for this repo's own
    name, and it resolves.
    """
    repo = make_repo(tmp_path, docs=(WANTED, DECOY), name="devrc")
    assert (repo / "claudedocs" / WANTED).is_file()
    if make_it:
        make_repo(tmp_path, docs=(), name=ydir)

    out = run_resume(repo, stub_bin, f"{ydir}/claudedocs/{WANTED}", cwd=repo)
    assert handoff_line(out) == "handoff: (none found — git-only)", out
    # The gap ECHOES the caller's own token, which contains WANTED — scrub that
    # one occurrence before asserting over the digest, or the test fails on the
    # very sentence that proves it behaved.
    scrubbed = out.replace(f"{ydir}/claudedocs/{WANTED}", "")
    for doc in (WANTED, DECOY):        # neither the named copy nor the newest
        assert doc not in scrubbed, f"{doc} was reconciled anyway\n{out}"
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert f"{ydir}/claudedocs/{WANTED}" in gaps[0], gaps
    assert gaps[0].endswith(GAP_REST_NONE), gaps


@pytest.mark.parametrize(
    "shape",
    [
        # <Y> is this checkout's own directory name — the kickoff-template shape
        pytest.param("devrc/claudedocs/{doc}", id="repo-prefixed"),
        # <Y> is empty — the token names no tree, so the cwd's is the only one
        pytest.param("claudedocs/{doc}", id="bare-claudedocs"),
    ],
)
def test_the_TWO_relative_shapes_that_DO_name_this_tree_still_resolve(
    tmp_path, stub_bin, shape
):
    """THE OTHER SIDE OF F1's DISCRIMINATOR, on the SAME fixture as the test
    above — so the miss there is attributable to the foreign `<Y>` and not to a
    worktree search that stopped working. Without this pair, gating the relative
    branch on `$mine` could be spelled `if false` and the miss test would still
    pass."""
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    add_worktree(repo, "devrc-topic", "feat/topic", docs=(IN_WORKTREE,))
    out = run_resume(repo, stub_bin, shape.format(doc=IN_WORKTREE), cwd=repo)
    assert handoff_line(out) == f"handoff: {IN_WORKTREE}", out
    assert gap_lines(out) == [], out


def test_a_SIBLING_WORKTREE_named_relatively_now_MISSES_and_the_absolute_form_does_not(
    tmp_path, stub_bin
):
    """⚠ THE NARROWING F1 TAKES ON PURPOSE, pinned so it is a decision and not
    a surprise.

    `devrc-topic/claudedocs/<base>` typed inside `devrc` names a sibling
    worktree of the SAME clone — legitimate, and indistinguishable BY NAME from
    the foreign `other-repo/...` above. It now misses. That is the safe
    direction: the run prints the gap naming exactly what it could not find
    rather than a confident digest, and the ABSOLUTE spelling of the very same
    path still resolves — which the second half here proves, so the narrowing
    costs a spelling and not a capability.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    wt = add_worktree(repo, "devrc-topic", "feat/topic", docs=(IN_WORKTREE,))

    rel = run_resume(repo, stub_bin, f"devrc-topic/claudedocs/{IN_WORKTREE}", cwd=repo)
    assert handoff_line(rel) == "handoff: (none found — git-only)", rel
    assert len(gap_lines(rel)) == 1, gap_lines(rel)

    absolute = run_resume(repo, stub_bin, str(wt / "claudedocs" / IN_WORKTREE), cwd=repo)
    assert handoff_line(absolute) == f"handoff: {IN_WORKTREE}", absolute
    assert gap_lines(absolute) == [], absolute


# --------------------------------------------------------------------------- #
# 🔴 #1197 AUDIT ROUND 1, F5 — A GLOB TOKEN IS NOT A DOCUMENT
#
# `set -f` keeps `claudedocs/handoff-*.md` from expanding, which is right, but
# it then reaches `[ -f ]` as a LITERAL that can never exist and was recorded as
# "the caller named a specific document". Since #1164 part 2 that suppresses the
# whole fallback chain, so the run reconciles NOTHING. That literal appears
# twice in /resume's own SKILL.md prose, which is passed through VERBATIM.
#
# MEASURED 2026-09-01, same repo, same command:
#   3e42bb04  handoff: (none found — git-only)  + "NO SUCH FILE" + NOTHING reconciled
#   HEAD      handoff: <the newest doc>         + the ordinary slug-miss gap
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "arg",
    [
        # the two spellings /resume's own SKILL.md carries
        pytest.param("claudedocs/handoff-*.md", id="star-lowercase-family"),
        pytest.param("claudedocs/*HANDOFF*.md", id="star-caps-family"),
        # the other two metacharacters `set -f` also literalises
        pytest.param("claudedocs/handoff-?.md", id="question-mark"),
        pytest.param("claudedocs/handoff-[ab].md", id="bracket"),
        # …and in prose, which is the form the skill actually passes through
        pytest.param("resume the claudedocs/handoff-*.md work", id="in-prose"),
    ],
)
def test_a_GLOB_shaped_token_is_not_recorded_as_a_named_document(
    tmp_path, stub_bin, arg
):
    """🔴 RED at 3e42bb04 for every row: `handoff: (none found — git-only)`.

    The repo holds real handoffs the fallback can serve, so "it reconciled
    something" is a fact about the guard rather than about an empty repo — and
    `test_a_star_in_the_argument_is_split_not_GLOBBED` above still pins that the
    token is not EXPANDED, which is the opposite hazard and must not regress.
    """
    docs = ("handoff-alpha-2026-01-01.md", WANTED, NEWEST)
    repo = make_repo(tmp_path, docs=docs)
    out = run_resume(repo, stub_bin, arg, cwd=repo)
    assert handoff_line(out) == f"handoff: {NEWEST}", out
    # the miss is still REPORTED — an argument that resolved nothing always
    # warns — but as the slug-miss lead, which does not claim a file was named.
    gaps = gap_lines(out)
    assert len(gaps) == 1, gaps
    assert "NO SUCH FILE" not in gaps[0], gaps
    assert "FELL BACK" in gaps[0], gaps


def test_the_glob_rows_above_are_not_vacuous_because_a_LITERAL_miss_still_gaps(
    tmp_path, stub_bin
):
    """POSITIVE CONTROL for the block above: the SAME repo, the SAME shape, one
    character different — no metacharacter — must still reconcile NOTHING.
    Without this the glob rows would pass against a `named_missing` that had
    stopped working altogether."""
    docs = ("handoff-alpha-2026-01-01.md", WANTED, NEWEST)
    repo = make_repo(tmp_path, docs=docs)
    out = run_resume(repo, stub_bin, "claudedocs/handoff-nope.md", cwd=repo)
    assert handoff_line(out) == "handoff: (none found — git-only)", out
    assert "NO SUCH FILE" in gap_lines(out)[0], gap_lines(out)


# --------------------------------------------------------------------------- #
# 🔴 #1197 AUDIT ROUND 1, F4 — THE CANDIDATE LIST'S ORDER
#
# Two independent defects in one line, both measured:
#   (a) `sort` was UNPINNED by locale while the expectations here are built with
#       Python `sorted()` (codepoint order = C order). Every fixture in this
#       module collates identically under C and under en_US.UTF-8, so none of
#       them could see it.
#   (b) the four shown were whichever four sorted first, and on the real clone
#       that is four disposable `.claude/worktrees/agent-*` checkouts — against
#       a sentence whose own advice is "pass the worktree's own path".
# --------------------------------------------------------------------------- #
def test_the_candidate_list_is_collated_LC_ALL_C_not_in_the_callers_locale(
    tmp_path, stub_bin
):
    """🔴 THIS TEST NEITHER SKIPS NOR DEGRADES — it FAILS if it cannot run.

    Same reasoning as `test_a_README_beside_a_lowercase_sibling_survives_a_UTF8_
    LOCALE` in test_subsystem_store_api.py, and the same mechanism supplies the
    locale to both tiers: `LOCALE_ARCHIVE`, exported in flake.nix's devShell AND
    in checks.pytests. A degradation to C would make this pass while observing
    nothing, which is the vacuity the pin exists to prevent.

    AVAILABILITY IS DETECTED BY EXERCISING THE CAPABILITY: sort the two fixture
    paths under C and under en_US.UTF-8 and require the orders to DIFFER. That
    one assertion answers "is a non-C collation available?" and "does this
    fixture still invert?" at once.

    The inversion: `devrc-Bravo` vs `devrc-alpha`. C compares 'B' (0x42) before
    'a' (0x61); en_US.UTF-8 folds case and puts 'a' before 'b'. Python
    `sorted()` is codepoint order, i.e. C order — so an unpinned `sort` under
    the caller's own locale emits the list in the OTHER order and this fails.
    """
    forced = "en_US.UTF-8"
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    wts = [
        add_worktree(repo, "devrc-Bravo", "feat/bravo", docs=(IN_WORKTREE,)),
        add_worktree(repo, "devrc-alpha", "feat/alpha", docs=(IN_WORKTREE,)),
    ]
    paths = [f"{w}/claudedocs/{IN_WORKTREE}" for w in wts]

    def _sorted_under(lc):
        return subprocess.run(
            ["sort"], input="".join(p + "\n" for p in paths),
            capture_output=True, text=True, env={**os.environ, "LC_ALL": lc},
        ).stdout

    assert _sorted_under("C") != _sorted_under(forced), (
        f"`sort` orders {paths} the same under C and under {forced}, so this "
        "test would pass whether or not the collation is pinned. Either the "
        "fixture stopped inverting, or — far more likely — this is a GATE "
        "ENVIRONMENT regression: flake.nix must export LOCALE_ARCHIVE in BOTH "
        "the devShell and checks.pytests, or the tier has only the C locale."
    )

    tok = repo / "claudedocs" / IN_WORKTREE
    gaps = gap_lines(run_resume(
        repo, stub_bin, str(tok), cwd=repo,
        extra_env={"LC_ALL": forced, "LANG": forced},
    ))
    assert len(gaps) == 1, gaps
    assert gaps[0] == GAP_LEAD_AMBIGUOUS.format(
        tok=tok, base=IN_WORKTREE, n=2, paths=", ".join(sorted(paths)),
    ) + GAP_REST_NONE, gaps[0]


def test_a_HUMAN_named_worktree_is_shown_ahead_of_EPHEMERAL_agent_checkouts(
    tmp_path, stub_bin
):
    """🔴 The gap's advice is "pass the worktree's own path", so the four it
    shows must be four a human could act on.

    MEASURED 2026-09-01 on this host's real devrc clone:
    `handoff-discord-embed-ext-rescue.md` exists in 28 worktrees, 27 of them
    `.claude/worktrees/agent-*` and ONE human-named (`devrc-handoff-cairn`).

    ⚠ WHAT THAT MEASUREMENT ACTUALLY SHOWED, since the two halves of F4
    interact: under the AMBIENT en_US.UTF-8 the human worktree came back at
    position 28 of 28 and was hidden inside `and 24 more`; under the `LC_ALL=C`
    now pinned it sorts FIRST. So the real instance is already fixed by the
    other half. This fixture therefore builds the case C order does NOT fix —
    a human worktree whose path sorts AFTER `<repo>/.claude/…` ('z' > '/') —
    because a sort order is not a reason to recommend a disposable checkout.
    """
    repo = make_repo(tmp_path, docs=(BASE_DECOY,), name="devrc")
    agents = [
        add_worktree(repo, f"devrc/.claude/worktrees/agent-{i}", f"feat/a{i}",
                     docs=(IN_WORKTREE,))
        for i in range(4)
    ]
    human = add_worktree(repo, "zz-human-worktree", "feat/human",
                         docs=(IN_WORKTREE,))
    a_paths = sorted(f"{w}/claudedocs/{IN_WORKTREE}" for w in agents)
    h_path = f"{human}/claudedocs/{IN_WORKTREE}"

    # THE FIXTURE IS DISCRIMINATING: in plain sorted order the human worktree
    # falls outside the four shown, so a run that still shows it can only be
    # doing so because of the preference.
    assert h_path not in sorted(a_paths + [h_path])[:4], (a_paths, h_path)

    tok = repo / "claudedocs" / IN_WORKTREE
    gaps = gap_lines(run_resume(repo, stub_bin, str(tok), cwd=repo))
    assert len(gaps) == 1, gaps
    # human first, then the agent checkouts in LC_ALL=C order, then the
    # UNCAPPED count of 5 and the `and 1 more` clause.
    assert gaps[0] == GAP_LEAD_AMBIGUOUS.format(
        tok=tok, base=IN_WORKTREE, n=5,
        paths=", ".join([h_path] + a_paths[:3]) + ", and 1 more",
    ) + GAP_REST_NONE, gaps[0]
