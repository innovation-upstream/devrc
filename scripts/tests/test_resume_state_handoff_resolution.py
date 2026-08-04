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
on a real timeout. Measured runtime of the whole module: well under a second.

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
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "resume-state.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git + bash on PATH",
)

# Docs that live beside a handoff in these repos and must NEVER resolve as one.
DECOY_DOCS = (
    "SOME-DESIGN.md",
    "COMFYUI-INTEGRATION-DESIGN.md",
    "SECURITY-AUDIT-v0.1.64.md",
    "LAUNCH-REDDIT-POST.md",
    "HUGGINGFACE-FALLBACK-RESEARCH.md",
    "BREADCRUMBS-AND-COPY-DESIGN.md",
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
        p = d / name
        p.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\n'
            "exit 1\n"
        )
        p.chmod(0o755)
    return d, log


def make_repo(tmp_path, docs=(), name="fixture-repo"):
    """A throwaway git repo whose claudedocs/ holds exactly `docs`.

    No `origin` remote (so SLUG stays empty and the PR block skips) and no
    prod-kubeconfig (so WORKLOAD/ALERTS skip). Files are created in the order
    given and stamped with increasing mtimes, so `docs[-1]` is the NEWEST —
    which is what `ls -t | head -1` selects.
    """
    repo = tmp_path / name
    (repo / "claudedocs").mkdir(parents=True)
    env = _git_env(repo)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "seed"], check=True, env=env
    )
    for i, doc in enumerate(docs):
        p = repo / "claudedocs" / doc
        p.write_text(f"## {doc}\nsome handoff prose\n")
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


def run_resume(repo, stub_bin, *args, cwd=None):
    """Run resume-state.sh with `repo` as cwd; return its stdout."""
    d, log = stub_bin
    env = _git_env(repo)
    env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    out = subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(cwd or repo),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, f"script failed rc={out.returncode}\n{out.stderr}"
    return out.stdout


def handoff_line(stdout):
    """The digest's `handoff:` line, stripped. Fails loudly if absent."""
    hits = [ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("handoff:")]
    assert len(hits) == 1, f"expected exactly one handoff: line, got {hits}\n{stdout}"
    return hits[0]


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
    """The honest-git-only wording must not leak onto the real clean path."""
    repo = make_repo(tmp_path, docs=("SESSION-HANDOFF.md",))
    out = run_resume(repo, stub_bin)
    assert handoff_line(out) == "handoff: SESSION-HANDOFF.md"
    joined = " ".join(drift_lines(out))
    assert "matches the handoff's claims" in joined, joined
    assert "no handoff" not in joined, joined


# --------------------------------------------------------------------------- #
# hermeticity
# --------------------------------------------------------------------------- #
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
