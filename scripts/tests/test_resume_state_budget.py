"""The `BUDGET` block: what a session may WRITE, put on screen BEFORE it composes.

WHAT THIS GATES
---------------
Every other mechanism that tells a session about its handoff budget speaks at
WRITE time. `handoff_doc.budget_warning` fires once the text is composed; rule
(p) REFUSES there (`size-ratchet`, exit 14); rules (q)/(r)/(s) give that refusal
exits (`--prune`, `--archive-write`, `--autoevict`). The session's first signal
was therefore a refusal arriving after the expensive part, and the second pass to
prune or evict is the token cost the operator kept reporting.

MEASURED at `3e7725bc`, which is what makes this a regression suite rather than a
feature suite: `origin/main`'s digest printed 8 top-level blocks and ZERO
`^BUDGET$` lines for `claudedocs/handoff-mention-detection.md` — a doc with
8,010 B of headroom. Its only matches for `budget|byte|ceiling` were 4 incidental
comment lines (1098, 1352, 2081, 2188). So the digest did not carry the number at
all, and every assertion below is RED at that ref by absence of the block.

🔴 THE SEAM IS `TestTheDigestAndTheWarningShareONEBand`, and it is the reason
this suite is not just a formatting check. The block and `budget_warning` are two
readers of one threshold in two languages; each is hermetically testable and both
can be green while disagreeing about a real document — the digest would then
promise headroom the writer does not honour. `claude/RULES.md`: "verified in
isolation is the new vacuous green — the defect lives in the SEAM nobody owns."

HERMETIC. Every fixture repo is a throwaway `git init` under tmp_path with no
remote. `gh`/`kubectl`/`curl`/`clawgatectl` are tripwire stubs on the front of
$PATH that log and fail, so no test reaches a network or a real board, and
`RESUME_STATE_SKILL` is emptied so the SKILL block cannot answer for this host.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

RESUME = REPO_ROOT / "scripts/resume-state.sh"
LIB = REPO_ROOT / "scripts/lib"
DOC_LIB = LIB / "handoff_doc.py"
PROBE = LIB / "handoff_budget_probe.py"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash + git on PATH",
)


def _load(name: str, path: Path):
    """A module by PATH, the way the rest of the suite loads `scripts/lib`.

    🔴 A FAILED IMPORT MUST BE AN ERROR, NOT AN EMPTY ANSWER — several
    assertions below are of the form "this input yields nothing", and a module
    that could not be loaded yields nothing too.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return mod


try:
    H = _load("handoff_doc_budgetblock", DOC_LIB)
    B = H.handoff_budget
except Exception as exc:  # pragma: no cover - surfaced as a skip, never a pass
    H = B = None
    _IMPORT_ERROR = exc

needs_lib = pytest.mark.skipif(H is None, reason="handoff_doc.py could not be imported")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _git_env(repo: Path) -> dict:
    env = dict(os.environ)
    for k in ("RESUME_STATE_INVESTIGATION_MAX_AGE_DAYS", "RESUME_STATE_SKILL_SCAN_CAP",
              "CLAUDE_CODE_SESSION_ID", "OPENCODE_SESSION_ID", "OPENCODE"):
        env.pop(k, None)
    env.update({
        "GIT_CONFIG_GLOBAL": str(repo.parent / "gitconfig-global"),
        "GIT_CONFIG_SYSTEM": str(repo.parent / "gitconfig-system"),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        # EMPTY, not unset: unset reads as "check /resume", which would make this
        # suite report on the HOST's deployed skill copy.
        "RESUME_STATE_SKILL": "",
    })
    return env


@pytest.fixture
def stubs(tmp_path_factory):
    """`gh`/`kubectl`/`curl`/`clawgatectl` tripwires: they log and fail."""
    d = tmp_path_factory.mktemp("budget-stubbin")
    log = d / "invocations.log"
    for name in ("gh", "kubectl", "curl", "clawgatectl"):
        write_exec(d / name, f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\nexit 1\n')
    return d, log


# 🔴 PADDED TO AN EXACT BYTE COUNT, because every assertion here is arithmetic
# about bytes. The filler is one long line of `z` rather than many short ones so
# the size is a simple subtraction and no newline accounting creeps in.
_HEAD = ("# Handoff: budget sample — 2026-01-01\n\n## Goal\n"
         "nothing outstanding\n\n## State now\n- nothing\n\n## Filler\n")


def doc_of_exactly(total: int) -> str:
    """A syntactically ordinary handoff doc of exactly `total` UTF-8 bytes.

    No PR numbers, no branch names, no `clawgate-task:` field and no investigation
    blocks, so every other block stays on its skip path and what lands in DRIFT is
    about this one.
    """
    pad = total - len(_HEAD.encode()) - 1  # -1 for the trailing newline
    assert pad > 0, f"{total} B is too small for the fixture header"
    return _HEAD + "z" * pad + "\n"


def make_repo(tmp_path: Path, *, size: int, name: str = "handoff-sample.md",
              gated: bool = True, text: str | None = None) -> Path:
    """A fixture repo holding ONE handoff doc of a chosen size.

    `gated` writes an EMPTY `scripts/tests/test_handoff_doc_size.py`, which is
    exactly what `gate_enforces_budget` asks about: a repo is gated iff it SHIPS
    the gate. The file's contents are irrelevant to that predicate, and relying on
    this repo's own checkout instead is the #1815-F1 error.
    """
    repo = tmp_path / f"fixture-{name}-{size}"
    (repo / "claudedocs").mkdir(parents=True)
    if gated:
        (repo / "scripts" / "tests").mkdir(parents=True)
        (repo / B.GATE_RELPATH).write_text("", encoding="utf-8")
    env = _git_env(repo)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    subprocess.run([*git, "add", "README.md"], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "seed"], check=True, env=env)
    (repo / "claudedocs" / name).write_text(
        text if text is not None else doc_of_exactly(size), encoding="utf-8")
    subprocess.run([*git, "add", f"claudedocs/{name}"], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "handoff"], check=True, env=env)
    return repo


def run_digest(repo: Path, stubs, *, script: Path | None = None, arg: str = "",
               locale: str | None = None) -> str:
    d, log = stubs
    env = _git_env(repo)
    env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    # 🔴 THE LOCALE IS SET EXPLICITLY, because leaving it inherited is what makes
    # a suite structurally blind to the dimension — and this block formats
    # numbers, so the locale IS one of its dimensions. See
    # `test_the_figures_are_separated_the_same_way_in_ANY_locale`.
    if locale is not None:
        env["LC_ALL"] = locale
    else:
        env.pop("LC_ALL", None)
    # No remote in these fixtures, so a fetch is pure latency; the freshness
    # reconciler's own tests own that path.
    env["RESUME_STATE_SKIP_FETCH"] = "1"
    out = subprocess.run(
        ["bash", str(script or RESUME), *([arg] if arg else [])],
        cwd=str(repo), capture_output=True, text=True, timeout=180, env=env)
    assert out.returncode == 0, f"rc={out.returncode}\n{out.stdout}\n{out.stderr}"
    return out.stdout


def section(out: str, name: str) -> list[str]:
    """The indented lines under a top-level digest header."""
    body, seen = [], False
    for line in out.splitlines():
        if line == name:
            seen = True
            continue
        if seen:
            if line and not line.startswith(" ") and not line.startswith("#"):
                break
            body.append(line)
    assert seen, f"no {name} block in:\n{out}"
    return body


def budget(out: str) -> str:
    return "\n".join(section(out, "BUDGET"))


def drift(out: str) -> str:
    return "\n".join(section(out, "DRIFT"))


# --------------------------------------------------------------------------- #
# the block exists at all — the whole defect
# --------------------------------------------------------------------------- #
@needs_lib
def test_the_digest_carries_a_BUDGET_block_naming_size_allowance_and_headroom(
        tmp_path, stubs):
    """🔴 THE REGRESSION. `origin/main` at 3e7725bc printed no such block for any
    document, so a session learned its budget only by being refused.

    Pinned as FOUR separate figures rather than one rendered string: a block that
    named the size but not the headroom would still leave the reader doing the
    subtraction that the refusal already does for them.
    """
    repo = make_repo(tmp_path, size=20_000)
    out = budget(run_digest(repo, stubs))
    assert "handoff-sample.md" in out, out
    assert "20,000 B" in out, out                        # current size
    assert f"{B.MAX_BYTES:,} B" in out, out              # the ceiling
    assert f"{65_536 - 20_000:,} B of headroom" in out, out
    assert f"{H.BUDGET_NEAR_BYTES:,} B" in out, out      # the band


@needs_lib
def test_a_doc_with_room_is_told_it_is_CLEAR_and_raises_no_drift(tmp_path, stubs):
    """🔴 THE NEGATIVE CONTROL THAT MAKES EVERY ⚠ BELOW WORTH READING. A block
    that flagged every document would be the permanently-red gate `claude/RULES.md`
    objects to, and widening a band (#2001) is exactly the change that turns a
    guard which fires on nothing into one that fires on everything.
    """
    repo = make_repo(tmp_path, size=20_000)
    out = run_digest(repo, stubs)
    assert "✅ clear of the warning band" in budget(out), budget(out)
    assert "INSIDE the warning band" not in budget(out)
    assert "OVER BUDGET" not in budget(out)
    assert "budget" not in drift(out).lower(), drift(out)


@needs_lib
def test_a_doc_INSIDE_the_band_is_warned_and_lands_in_DRIFT(tmp_path, stubs):
    """Derived from the band, never a literal: `MAX_BYTES - band + 5,000` sits
    strictly inside it at any band width, and 5,000 is not a multiple of the band
    so the fixture cannot land ON the boundary the branch tests.
    """
    size = B.MAX_BYTES - H.BUDGET_NEAR_BYTES + 5_000
    repo = make_repo(tmp_path, size=size)
    out = run_digest(repo, stubs)
    assert "INSIDE the warning band" in budget(out), budget(out)
    assert f"{B.MAX_BYTES - size:,} B of headroom" in budget(out)
    assert "INSIDE its warning band" in drift(out), drift(out)
    assert "BEFORE composing" in drift(out)


@needs_lib
def test_a_doc_OVER_budget_is_told_rule_p_REFUSES_and_lands_in_DRIFT(tmp_path, stubs):
    """The over arm must name the REFUSAL, not merely the overage: the exits that
    clear it (`--prune`/`--archive-write`/`--autoevict`) are what a session needs
    before it composes, and discovering them after exit 14 is the second pass.
    """
    size = B.MAX_BYTES + 4_472
    repo = make_repo(tmp_path, size=size)
    out = run_digest(repo, stubs)
    assert "🔴 OVER BUDGET" in budget(out), budget(out)
    assert "OVER BY 4,472 B" in budget(out), budget(out)
    assert "size-ratchet" in budget(out)
    for flag in ("--prune", "--archive-write", "--autoevict",
                 "--override-size-ratchet"):
        assert flag in budget(out), f"{flag} missing from:\n{budget(out)}"
    assert "OVER its budget" in drift(out), drift(out)


@needs_lib
def test_a_NEGATIVE_headroom_is_never_printed_as_headroom(tmp_path, stubs):
    """⚠ INVARIANT GUARD, labelled as one: no measured bug produced this. It is
    here because `allowance - bytes` is signed and the over arm shares the header
    line, so the obvious implementation prints "-4,472 B of headroom" — right
    arithmetic, reads as a typo, on the one line a session acts on.
    """
    repo = make_repo(tmp_path, size=B.MAX_BYTES + 4_472)
    out = budget(run_digest(repo, stubs))
    assert "-4,472" not in out, out
    assert "of headroom" not in out, out


# --------------------------------------------------------------------------- #
# the gate claim — getting this backwards is the civitai/cli#618 shape
# --------------------------------------------------------------------------- #
@needs_lib
class TestTheRedTestClaimIsRepoAwareAndTheREFUSALIsNot:
    """🔴 TWO CLAIMS WITH DIFFERENT SCOPES, ASSERTED IN BOTH DIRECTIONS.

    Rule (p) refuses growth past the allowance AT WRITE TIME IN ANY REPO —
    `handoff_budget`'s module docstring says so. Only
    `test_no_handoff_doc_exceeds_its_budget` going red for everyone is
    devrc-only, because that test enumerates its OWN tree. Announcing the red to
    a repo no gate reads is what cost civitai/cli#618 35,517 B of evictions
    against a gate that could not see the repo, and `gate_enforces_budget`'s
    docstring is the record.
    """

    def test_a_GATED_repo_is_told_the_test_goes_red(self, tmp_path, stubs):
        repo = make_repo(tmp_path, size=B.MAX_BYTES + 4_472, gated=True)
        out = budget(run_digest(repo, stubs))
        assert "RED for everyone" in out, out
        assert "REFUSES any update that GROWS" in out, out

    def test_an_UNGATED_repo_is_told_the_REFUSAL_but_NOT_the_red(
            self, tmp_path, stubs):
        repo = make_repo(tmp_path, size=B.MAX_BYTES + 4_472, gated=False)
        out = budget(run_digest(repo, stubs))
        assert "RED for everyone" not in out, out
        # …and the refusal survives, because it is true in every repo. Without
        # this half the test would pass on a block that said nothing at all.
        assert "REFUSES any update that GROWS" in out, out
        assert "size-ratchet" in out, out


# --------------------------------------------------------------------------- #
# the grandfathered allowance comes from the LEDGER, not from MAX_BYTES
# --------------------------------------------------------------------------- #
@needs_lib
def test_a_GRANDFATHERED_doc_is_sized_against_its_LEDGER_allowance(tmp_path, stubs):
    """🔴 A doc over `MAX_BYTES` but inside its allowance is NOT over budget, and
    reporting it as over would flag 11 of devrc's 108 documents on every resume.

    The entry is chosen from the LIVE ledger rather than named, because the ledger
    is a ratchet: entries are DELETED when a doc prunes back under the ceiling, so
    a hardcoded key is a test that breaks on an unrelated eviction. Keyed on the
    relpath alone, which is why a fixture repo can carry the name.
    """
    devrc = {k: v for k, v in B.GRANDFATHERED.items()
             if k.startswith("claudedocs/handoff-") and not B.is_foreign_key(k)}
    if not devrc:
        pytest.skip("the GRANDFATHERED ledger carries no plaintext devrc entry")
    key, allowance = max(devrc.items(), key=lambda kv: kv[1])
    name = key.split("/", 1)[1]
    # Strictly between MAX_BYTES and the allowance, and not ON either edge.
    size = B.MAX_BYTES + (allowance - B.MAX_BYTES) // 2
    repo = make_repo(tmp_path, size=size, name=name)
    out = run_digest(repo, stubs)
    assert f"{allowance:,} B" in budget(out), budget(out)
    assert "its grandfathered allowance" in budget(out), budget(out)
    assert "OVER BUDGET" not in budget(out), budget(out)
    assert "budget" not in drift(out).lower(), drift(out)


@needs_lib
def test_the_figures_are_separated_the_same_way_in_ANY_locale(tmp_path, stubs):
    """🔴 MEASURED, AND IT IS WHY THE PROBE FORMATS THE NUMBERS.

    The first implementation formatted in the shell with `printf "%'d"`, which is
    LOCALE-DEPENDENT: measured on this host, `LC_ALL=C printf "%'d" 65536` prints
    `65536` and the UTF-8 locale prints `65,536`. `budget_warning` uses Python's
    `{:,}` and always separates, so the digest and the warning would have
    disagreed about how to spell the same number — and a suite that inherits the
    locale cannot see it, which is the config-blindness `claude/RULES.md` names.

    Both locales asserted, because pinning one is the defect.
    """
    repo = make_repo(tmp_path, size=20_000)
    for loc in ("C", "C.UTF-8"):
        out = budget(run_digest(repo, stubs, locale=loc))
        assert "20,000 B" in out, f"LC_ALL={loc}:\n{out}"
        assert f"{B.MAX_BYTES:,} B" in out, f"LC_ALL={loc}:\n{out}"


@needs_lib
def test_a_doc_OUTSIDE_claudedocs_does_not_INHERIT_a_colliding_ledger_entry(
        tmp_path, stubs):
    """🔴 MEASURED DURING IMPLEMENTATION, AND IT IS WHY THE KEY IS ASKED OF GIT.

    `resume-state.sh`'s explicit-path branch accepts any existing file, so
    `resume-state.sh docs/handoff-<topic>.md` resolves a doc outside
    `claudedocs/`. Deriving the ledger key by re-prefixing
    `handoff_ref_for_exclusion` — which yields the path from `claudedocs/` DOWN,
    i.e. the BASENAME for such a doc — fabricates `claudedocs/<basename>` and
    resolves a DIFFERENT document's entry.

    MEASURED on a 99,998 B `docs/handoff-tmux-webapp.md` in a fixture repo: the
    fabricated key reported "99,998 B of 180,224 B (its grandfathered allowance)
    — 80,226 B of headroom ✅", inheriting the allowance of a `claudedocs/`
    document it merely shares a basename with. Git's own `ls-files --full-name`
    answer makes the key the real relpath, and nothing in `claudedocs/` governs a
    file in `docs/`.
    """
    devrc = {k: v for k, v in B.GRANDFATHERED.items()
             if k.startswith("claudedocs/handoff-") and not B.is_foreign_key(k)}
    if not devrc:
        pytest.skip("the GRANDFATHERED ledger carries no plaintext devrc entry")
    key, allowance = max(devrc.items(), key=lambda kv: kv[1])
    name = key.split("/", 1)[1]

    repo = tmp_path / "collide"
    (repo / "docs").mkdir(parents=True)
    (repo / B.GATE_RELPATH).parent.mkdir(parents=True)
    (repo / B.GATE_RELPATH).write_text("", encoding="utf-8")
    env = _git_env(repo)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=env)
    # Over MAX_BYTES, but comfortably under the colliding entry's allowance — so
    # a mis-key reads as a cheerful ✅ rather than as any kind of warning.
    size = B.MAX_BYTES + (allowance - B.MAX_BYTES) // 2
    (repo / "docs" / name).write_text(doc_of_exactly(size), encoding="utf-8")

    out = run_digest(repo, stubs, arg=f"docs/{name}")
    assert "no size budget governs this document" in budget(out), budget(out)
    assert f"{allowance:,}" not in budget(out), budget(out)
    assert "grandfathered" not in budget(out), budget(out)


# --------------------------------------------------------------------------- #
# THE SEAM — one threshold, two readers, two languages
# --------------------------------------------------------------------------- #
@needs_lib
class TestTheDigestAndTheWarningShareONEBand:
    """🔴 THE RELATIONSHIP, NOT THE COMPONENTS. The digest promises headroom and
    `budget_warning` enforces the same edge; both are hermetically testable and
    both can be green while disagreeing, at which point the digest's ✅ is a
    promise the writer does not keep. So assert the edge AGREES, from both sides,
    over the same numbers.
    """

    def test_the_band_the_digest_PRINTS_is_the_one_the_warning_READS(
            self, tmp_path, stubs):
        repo = make_repo(tmp_path, size=20_000)
        assert f"warning band {H.BUDGET_NEAR_BYTES:,} B" in budget(
            run_digest(repo, stubs))

    def test_the_digest_flips_to_INSIDE_on_the_SAME_byte_the_warning_does(
            self, tmp_path, stubs):
        """The +-1 pair across the edge, on both implementations at once. A
        structural check that merely read the constant would type-check past an
        off-by-one in either reader; this one cannot.
        """
        edge = B.MAX_BYTES - H.BUDGET_NEAR_BYTES   # widest size still CLEAR
        rel = "claudedocs/handoff-sample.md"

        def warns(size: int) -> bool:
            return bool(H.budget_warning(rel, "x" * size, "x" * (size - 1),
                                         gated=True))

        def digest_says_inside(size: int) -> bool:
            repo = make_repo(tmp_path, size=size)
            return "INSIDE the warning band" in budget(run_digest(repo, stubs))

        assert warns(edge) is False and digest_says_inside(edge) is False
        assert warns(edge + 1) is True and digest_says_inside(edge + 1) is True

    def test_the_ceiling_the_digest_PRINTS_is_handoff_budgets_MAX_BYTES(
            self, tmp_path, stubs):
        repo = make_repo(tmp_path, size=20_000)
        assert f"of {B.MAX_BYTES:,} B" in budget(run_digest(repo, stubs))

    def test_handoff_doc_RE_EXPORTS_the_band_rather_than_re_declaring_it(self):
        """🔴 THE CONSOLIDATION, PINNED. #2001 moved `BUDGET_NEAR_BYTES` into
        `handoff_budget` (stdlib-only) because the probe cannot import
        `handoff_doc` — see `test_the_probe_does_NOT_import_handoff_doc`.
        `handoff_doc` keeps the name as a RE-EXPORT.

        Asserted as object identity, not equality: two independently-declared
        ints with the same value compare equal, which is exactly the drift this
        is here to catch.
        """
        assert H.BUDGET_NEAR_BYTES is B.BUDGET_NEAR_BYTES

    def test_the_two_budget_PREDICATES_are_also_one_implementation(self):
        """The same consolidation for `is_handoff_doc` and the gate check, over
        inputs that span both answers so a stubbed-out predicate cannot pass.
        """
        for rel in ("claudedocs/handoff-x.md", "claudedocs/archive/handoff-x.md",
                    "claudedocs/proposal-x.md", "README.md",
                    "claudedocs/SESSION-HANDOFF.md"):
            assert (H.budget_position(rel, "", "").is_handoff_doc
                    is B.is_handoff_doc(rel)), rel
        # …and both answers are actually produced, or the loop proves nothing.
        assert B.is_handoff_doc("claudedocs/handoff-x.md")
        assert not B.is_handoff_doc("claudedocs/proposal-x.md")


# --------------------------------------------------------------------------- #
# degraded paths — a reason, never a reassuring zero
# --------------------------------------------------------------------------- #
@needs_lib
class TestItDegradesLoudlyAndInTheRIGHTCHANNEL:
    """🔴 TWO FAILURES THAT ARE NOT THE SAME KIND OF FAILURE. A doc nothing
    budgets is a fact about the document; a probe that could not run is a source
    that did not answer. Routing the first to the `!! GAPS` banner would fire it
    on every `claudedocs/*HANDOFF*.md` resume, which is `claude/RULES.md`'s
    permanently-red-gate objection — `dod_block` took the same ruling for a doc
    with no closing-condition field.
    """

    def test_no_handoff_resolved_says_so_rather_than_printing_a_zero(
            self, tmp_path, stubs):
        repo = tmp_path / "empty-repo"
        (repo / "claudedocs").mkdir(parents=True)
        env = _git_env(repo)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=env)
        out = budget(run_digest(repo, stubs))
        assert "no handoff" in out, out
        assert "0 B" not in out, out

    def test_a_doc_NOTHING_budgets_is_not_reported_as_a_GAP(self, tmp_path, stubs):
        """`resume-state.sh` also resolves `claudedocs/*HANDOFF*.md`, which the
        ceiling does not enumerate. Both halves asserted: the block says so, AND
        the gap banner stays silent about it.
        """
        repo = make_repo(tmp_path, size=3_000, name="SESSION-HANDOFF.md",
                         text="# session handoff\n\n## Goal\nnothing\n")
        out = run_digest(repo, stubs)
        assert "no size budget governs this document" in budget(out), budget(out)
        assert "BUDGET unknown" not in out, out

    def test_an_UNIMPORTABLE_module_degrades_to_ONE_line_with_the_exception(
            self, tmp_path, stubs):
        """🔴 THE GAP CHANNEL IS LINE-ORIENTED, and the common failure here is a
        PYTHON TRACEBACK — i.e. always multi-line. `print_gaps` emits
        `printf '  ! %s\\n'` per entry, so an unclipped traceback breaks the `!`
        prefix the /resume skill keys on.

        MEASURED with the probe PRESENT but `handoff_doc.py` absent from its lib
        directory: the raw form spilled four lines into the block and three more
        into the banner. Asserted three ways — the exception survives, the frame
        noise does not, and the block's line stays single.
        """
        shim = tmp_path / "brokenlib"
        (shim / "lib").mkdir(parents=True)
        shutil.copy2(RESUME, shim / "resume-state.sh")
        shutil.copy2(PROBE, shim / "lib" / PROBE.name)
        for extra in (LIB / "clawgate_handoff.sh",):
            if extra.exists():
                shutil.copy2(extra, shim / "lib" / extra.name)
        # handoff_doc.py / handoff_budget.py deliberately NOT copied.
        repo = make_repo(tmp_path, size=40_000)
        out = run_digest(repo, stubs, script=shim / "resume-state.sh")
        block = budget(out)
        assert "cannot size this doc" in block, block
        assert "ModuleNotFoundError" in block, block
        assert "Traceback" not in block, block
        assert 'File "' not in block, block
        reason_lines = [ln for ln in block.splitlines()
                        if "cannot size this doc" in ln]
        assert len(reason_lines) == 1, block
        assert "BUDGET unknown" in out and "GAPS" in out, out
        assert "none detected" not in drift(out), drift(out)

    def test_a_MISSING_probe_is_a_named_GAP_and_not_a_silent_skip(
            self, tmp_path, stubs):
        """🔴 THE POSITIVE CONTROL ON THE GAP CHANNEL ITSELF. A resume that could
        not measure the doc must say which source went unanswered — an absent
        BUDGET figure that reads as "fine" is the failure this whole block exists
        to remove.

        The script is copied WITHOUT `lib/handoff_budget_probe.py`, which is the
        real deployment shape of the failure: `resume-state.sh` resolves the probe
        beside itself.
        """
        shim = tmp_path / "shimdir"
        (shim / "lib").mkdir(parents=True)
        shutil.copy2(RESUME, shim / "resume-state.sh")
        for extra in (LIB / "clawgate_handoff.sh",):
            if extra.exists():
                shutil.copy2(extra, shim / "lib" / extra.name)
        assert not (shim / "lib" / PROBE.name).exists()
        repo = make_repo(tmp_path, size=20_000)
        out = run_digest(repo, stubs, script=shim / "resume-state.sh")
        assert "cannot size this doc" in budget(out), budget(out)
        assert "BUDGET unknown" in out, out
        assert "GAPS" in out, out
        # …and the all-clear must be WITHHELD: a gap alongside no findings must
        # not print "live state matches the handoff's claims".
        assert "none detected" not in drift(out), drift(out)


# --------------------------------------------------------------------------- #
# the probe as a unit — and its own instrument controls
# --------------------------------------------------------------------------- #
@needs_lib
class TestTheProbeItself:
    """Exit codes are a contract here: `resume-state.sh` BRANCHES on 2 vs 1, and a
    field that exists is not a guard — only a branch on it is.
    """

    def _run(self, repo: Path, rel: str, text: str):
        return subprocess.run([sys.executable, str(PROBE), str(repo), rel],
                              input=text, capture_output=True, text=True,
                              timeout=60)

    def test_it_answers_TSV_facts_for_a_handoff_doc(self, tmp_path):
        got = self._run(tmp_path, "claudedocs/handoff-x.md", "y" * 20_000)
        assert got.returncode == 0, got.stderr
        facts = dict(line.split("\t", 1) for line in got.stdout.splitlines())
        assert facts["bytes"] == "20000"
        assert facts["allowance"] == str(B.MAX_BYTES)
        assert facts["band"] == str(H.BUDGET_NEAR_BYTES)
        assert facts["zone"] == "clear"

    def test_it_exits_2_for_a_doc_no_ceiling_governs(self, tmp_path):
        for rel in ("claudedocs/proposal-x.md", "README.md",
                    "claudedocs/SESSION-HANDOFF.md"):
            got = self._run(tmp_path, rel, "y" * 20_000)
            assert got.returncode == 2, f"{rel}: rc={got.returncode} {got.stderr}"
            assert "no budget applies" in got.stderr

    def test_it_exits_1_on_BAD_USAGE_rather_than_printing_a_zero(self, tmp_path):
        got = subprocess.run([sys.executable, str(PROBE), str(tmp_path)],
                             input="", capture_output=True, text=True, timeout=60)
        assert got.returncode == 1, got
        assert got.stdout == "", got.stdout
        assert "usage:" in got.stderr

    def test_the_zone_moves_with_the_SIZE_which_is_the_positive_control(
            self, tmp_path):
        """🔴 A reassuring `clear` is indistinguishable from a probe wired to
        nothing. Feed it three sizes that MUST land in three different zones and
        watch the value move; report of a single zone proves nothing.
        """
        edge = B.MAX_BYTES - H.BUDGET_NEAR_BYTES
        seen = []
        for size in (edge, edge + 5_000, B.MAX_BYTES + 1):
            got = self._run(tmp_path, "claudedocs/handoff-x.md", "y" * size)
            assert got.returncode == 0, got.stderr
            facts = dict(line.split("\t", 1) for line in got.stdout.splitlines())
            seen.append(facts["zone"])
        assert seen == ["clear", "band", "over"], seen

    def test_the_probe_does_NOT_import_handoff_doc(self, tmp_path):
        """🔴 THE REGRESSION, AND IT WAS FOUND BY A TEST IN ANOTHER FILE.

        `handoff_doc.py` calls `cairn_pin.ensure()` at module scope, so importing
        it needs the pinned `cairn` client on PATH or `$CAIRN_LIB` set. The first
        version of this probe imported it, and on a host with neither it died on
        `CairnPinUnresolved` — so the digest reported the budget as an UNKNOWN gap
        on exactly the machines `clawgate_block`'s fallback exists for (one whose
        `home-manager switch` has not landed). `test_resume_state_clawgate.py`'s
        `assert not gaps(out)` is what caught it; nothing in this file did.

        Asserted TWO ways, because either alone is weak: the import is absent from
        the source (structural), and the probe actually ANSWERS when the import
        would have failed (behavioural). The behavioural half runs the probe with
        `$CAIRN_LIB` cleared and a PATH carrying only the interpreter, which is
        the condition that produced the failure.
        """
        src = PROBE.read_text(encoding="utf-8")
        code = [ln for ln in src.splitlines()
                if ln.startswith(("import ", "from ")) and "handoff_doc" in ln]
        assert not code, code

        bindir = tmp_path / "only-python"
        bindir.mkdir()
        (bindir / "python3").symlink_to(sys.executable)
        env = {k: v for k, v in os.environ.items()
               if k not in ("CAIRN_LIB", "PATH", "PYTHONPATH")}
        env["PATH"] = str(bindir)
        got = subprocess.run([str(bindir / "python3"), str(PROBE),
                              str(tmp_path), "claudedocs/handoff-x.md"],
                             input="y" * 20_000, capture_output=True, text=True,
                             timeout=60, env=env)
        assert got.returncode == 0, f"{got.returncode}\n{got.stderr}"
        assert "bytes\t20000" in got.stdout, got.stdout
        # The positive control on the stripped environment itself: it really is
        # one where importing handoff_doc fails, or this proves nothing.
        ctl = subprocess.run([str(bindir / "python3"), "-c",
                              f"import sys; sys.path.insert(0, {str(LIB)!r});"
                              " import handoff_doc"],
                             capture_output=True, text=True, timeout=60, env=env)
        assert ctl.returncode != 0, (
            "the stripped environment imports handoff_doc fine, so the case this "
            "test exists for was never reproduced:\n" + ctl.stderr)

    def test_it_sizes_the_TEXT_IT_IS_GIVEN_not_the_file_on_disk(self, tmp_path):
        """⚠ `resume-state.sh` may reconcile the `origin/<default>` copy rather
        than the working-tree one, so sizing the path would report a budget for a
        copy the digest did not read. Proved by handing it a relpath whose file
        does not exist at all, with text of a known size.
        """
        got = self._run(tmp_path, "claudedocs/handoff-nonexistent.md", "y" * 31_337)
        assert got.returncode == 0, got.stderr
        assert "bytes\t31337" in got.stdout, got.stdout
