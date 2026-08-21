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
    assert "PR #4101 UNATTRIBUTED" in out and "PR #4102 UNATTRIBUTED" in out, out
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
    assert "PR #4114 UNATTRIBUTED" in out, out
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
