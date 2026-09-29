#!/usr/bin/env python3
"""Gate on `scripts/cairn-ops/` — the four deterministic entry points onto the
cairn surface, and the skills that route to them instead of carrying a recipe.

WHY THIS EXISTS
---------------
Ten skills referenced the `cairn` surface and carried roughly 130 invocations
between them; four of them overlapped on it and several held their own inline copy
of a recipe. `claude/RULES.md`: "A predicate open-coded at N sites is typically
wrong at N−1 of them *in the same direction*, and unifying them is what makes the
disagreement audible." Unifying them made three disagreements audible, and all
three are now mechanical rather than prose:

  1. the guarded `command -v cairn >/dev/null` preflight appeared VERBATIM in
     three files and printed `skipped: cairn unavailable` at exit **0** — correct
     for a best-effort preflight, and exactly wrong for the mandated post-write
     check, which the same shape was also used for;
  2. no recipe anywhere resolved the store PER INSTANCE, so every one of them read
     whatever cache the client defaulted to;
  3. `cairn sync && cairn-validate --scope <scope>` was stated in three places, and
     `claude/skills/cairn/SKILL.md` records that two of them had already DRIFTED.

🔴 THE HERMETIC/LIVE SPLIT, STATED RATHER THAN LEFT TO BE INFERRED. Every test in
this module is HERMETIC: it builds a synthetic two-instance world in `tmp_path`,
puts stub `cairn` / `cairn-validate` executables on PATH, and never touches a real
pod, a real cache or the network. That is what makes it runnable in the nix build
sandbox, which has none of those. The LIVE measurements that motivated each guard
were run by hand against this host's real two-instance deployment and are recorded
in the commit message and in the docstrings below — `claude/RULES.md` asks for the
measurement, not for the measurement to be the gate.

🔴 AND THE SYNTHETIC WORLD IS BUILT SO THE DEFECT IS REPRODUCIBLE IN IT. The stub
`cairn-validate` counts the `.md` files under `<store>/<scope>/` — nothing more —
so pointing it at the DEFAULT cache for a scope that lives on the `beta` instance
makes it print `checked: 0` and exit **0**, which is precisely what the real tool
did on this host on 2026-09-27. A fixture that could not reproduce the defect could
not prove the fix.

Every identifier in the fixture is INVENTED. Nothing here is read out of a real
deployment: the instances are `personal` and `beta`, and the scopes are
`alpha-notes` (default instance) and `beta-notes` (non-default).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import mockbin  # noqa: E402

OPS = REPO / "scripts" / "cairn-ops"
SKILLS = REPO / "claude" / "skills"

#: The four entry points. A fifth file in that directory that is not `common.sh`
#: reds `test_the_directory_holds_exactly_the_four_entry_points` — the shape was an
#: operator decision, so growing it is a decision too, not a drive-by.
SCRIPTS = ("read.sh", "write.sh", "hygiene.sh", "health.sh")

# --------------------------------------------------------------------------- #
# THE EXIT-CODE LEDGER'S TWO PARSERS
#
# 🔴 BOTH ARE FUNCTIONS, AND BOTH ARE THEMSELVES CONTROLLED BELOW. A ledger test
# whose parser silently matched nothing would compare two empty sets and pass —
# the reassuring-zero shape. `TestTheLedgerParsersCanSee` feeds each one a case it
# MUST see and watches the number move.
# --------------------------------------------------------------------------- #

#: A printed legend row: two spaces, the code, whitespace, then its meaning.
_PRINTED = re.compile(r"^  (\d+)\s{2,}\S")
#: A declaration: `readonly EXIT_<NAME>=<n>`.
_DECLARED = re.compile(r"^readonly (EXIT_[A-Z_]+)=(\d+)\s*$", re.M)
#: A use: `exit "$EXIT_<NAME>"` or `exit $EXIT_<NAME>`.
_USED = re.compile(r'\bexit\s+"?\$(EXIT_[A-Z_]+)"?')
#: A BARE numeric exit — banned, because a literal cannot be cross-checked against
#: the printed legend and is how a code ships undocumented.
_BARE = re.compile(r"^\s*exit\s+(\d+)\s*$", re.M)


def printed_codes(help_text: str) -> set[int]:
    """The codes a script's `--help` documents, read from its `exit codes:` block."""
    out: set[int] = set()
    seen_header = False
    for line in help_text.splitlines():
        if line.strip() == "exit codes:":
            seen_header = True
            continue
        if not seen_header:
            continue
        hit = _PRINTED.match(line)
        if hit:
            out.add(int(hit.group(1)))
    return out


def declared_codes(*sources: str) -> dict[str, int]:
    """Every `readonly EXIT_*` across the given sources, as name -> value."""
    out: dict[str, int] = {}
    for src in sources:
        for name, value in _DECLARED.findall(src):
            out[name] = int(value)
    return out


def used_code_names(*sources: str) -> set[str]:
    """Every `EXIT_*` name an `exit` statement in the given sources names."""
    out: set[str] = set()
    for src in sources:
        out |= set(_USED.findall(src))
    return out


# --------------------------------------------------------------------------- #
# THE SYNTHETIC TWO-INSTANCE WORLD
# --------------------------------------------------------------------------- #


@dataclass
class World:
    root: Path
    bin: Path
    default_cache: Path
    beta_cache: Path
    calls: Path

    @property
    def env(self) -> dict[str, str]:
        e = dict(os.environ)
        e["PATH"] = f"{self.bin}{os.pathsep}{e.get('PATH', '')}"
        e["CAIRN_OPS_CALLS"] = str(self.calls)
        e["TMPDIR"] = str(self.root / "tmp")
        return e

    def run(self, script: str, *args: str, cwd: Path | None = None,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(OPS / script), *args],
            capture_output=True, text=True,
            cwd=str(cwd or self.root), env=env or self.env, timeout=60,
        )


#: 🔴 THE STUB IS THE FIXTURE'S LOAD-BEARING PART, so its behaviour is spelled out
#: rather than left to be read off the sh. It answers only the four questions the
#: scripts actually ask, and it answers them in the LIVE client's format:
#:
#:   recall --scope S --no-sync   ->  "  store: <cache for S's instance>"
#:   routes --no-sync             ->  "instances: personal, beta" + "  <s> -> <a>"
#:   ls-entries --no-sync         ->  "[<alias>] <scope>/<entry>.md", every instance
#:   doctor --json --no-sync      ->  {"checks":[{"name":"<a>/reader-resolution",…}]}
#:
#: ⚠ `ls-entries` IGNORING `--scope` IS NOT A SIMPLIFICATION — it is what the real
#: client does. Measured 2026-09-27: `cairn ls-entries --scope civitai-developer-docs
#: --no-sync` printed 437 lines, every entry on both instances. An earlier draft of
#: `hygiene.sh` counted those lines as "entries in this scope", so its emptiness
#: test was satisfied 437 times over and could never fire. A stub that filtered
#: would have hidden that.
_CAIRN_STUB = """\
CALLS="${{CAIRN_OPS_CALLS:-/dev/null}}"
DEFAULT_CACHE="{default_cache}"
BETA_CACHE="{beta_cache}"
verb="$1"; shift
printf '%s %s\\n' "$verb" "$*" >>"$CALLS"

scope_of() {{
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --scope) shift; printf '%s' "$1"; return 0 ;;
      --scope=*) printf '%s' "${{1#--scope=}}"; return 0 ;;
    esac
    shift
  done
}}

alias_of() {{
  case "$1" in
    beta-notes) printf beta ;;
    alpha-notes) printf personal ;;
    *) return 1 ;;
  esac
}}

cache_of() {{
  case "$1" in
    beta) printf '%s' "$BETA_CACHE" ;;
    personal) printf '%s' "$DEFAULT_CACHE" ;;
    *) return 1 ;;
  esac
}}

case "$verb" in
  recall)
    s=$(scope_of "$@")
    a=$(alias_of "$s") || {{ echo "cairn: scope \\`$s\\` is not routed" >&2; exit 11; }}
    echo "subsystem-recall: status=recalled scope=$s"
    echo "  store: $(cache_of "$a")"
    exit 0 ;;
  routes)
    echo "instances: personal, beta"
    echo "  alpha-notes -> personal"
    echo "  beta-notes -> beta"
    exit 0 ;;
  ls-entries)
    for a in personal beta; do
      c=$(cache_of "$a")
      for f in "$c"/*/*.md; do
        [ -e "$f" ] || continue
        echo "[$a] $(basename "$(dirname "$f")")/$(basename "$f")"
      done
    done
    exit 0 ;;
  doctor)
    printf '{{"checks":['
    printf '{{"name":"personal/reader-resolution","state":"OK",'
    printf '"detail":"the reader resolves %s, which carries a sync stamp"}},' "$DEFAULT_CACHE"
    printf '{{"name":"beta/reader-resolution","state":"OK",'
    printf '"detail":"the reader resolves %s, which carries a sync stamp"}}' "$BETA_CACHE"
    printf ']}}\\n'
    # 🔴 EXIT 10, NOT 0, AND DELIBERATELY. `cairn doctor` answers 10 whenever any
    # check COULD NOT LOOK, which `--no-sync` guarantees. `health.sh` branched on
    # that pipeline's status once and refused every correct answer on every host.
    exit 10 ;;
  search)
    q="$1"
    s=$(scope_of "$@")
    a=$(alias_of "$s") || {{ echo "cairn: scope \\`$s\\` is not routed" >&2; exit 11; }}
    echo "subsystem-recall: status=search-hit scope=$s query='$q'"
    echo "  store: $(cache_of "$a")"
    case "$q" in
      *zzz*) echo "NO MATCH — searched 2 entries in \\`$s/\\`, and nothing cleared the threshold" ;;
      *) echo "SEARCH (from index) — 3 of 7 hunks at or above 0.60, from 2 entries in \\`$s/\\`:" ;;
    esac
    exit 0 ;;
  sync) exit 0 ;;
  append|put|create)
    s=$(scope_of "$@")
    a=$(alias_of "$s") || exit 11
    echo "cairn: wrote instance=$a scope=$s"
    exit 0 ;;
  *) echo "cairn: unknown verb $verb" >&2; exit 2 ;;
esac
"""

#: The stub write-protocol checker. It counts `.md` files under `<store>/<scope>/`,
#: prints the real tool's two lines, and exits 0 either way — INCLUDING when it
#: counted nothing, which is the whole defect.
_VALIDATE_STUB = """\
store=""
scope=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --store) shift; store="$1" ;;
    --scope) shift; scope="$1" ;;
  esac
  shift
done
n=0
for f in "$store/$scope"/*.md; do
  [ -e "$f" ] || continue
  n=$((n + 1))
done
echo "subsystem-touch validate: \\`$scope/\\`"
echo "  store: $store"
if [ "$n" -eq 0 ]; then
  echo "NOTHING WAS CHECKED — no entry files were found for \\`$scope/\\`. A zero here is NOT a clean bill of health."
  exit 0
fi
echo "  checked: $n entry file(s)"
echo "OK — $n of $n entry file(s) parse, 0 malformed."
exit 0
"""


@pytest.fixture
def world(tmp_path: Path) -> World:
    """A two-instance world: `alpha-notes` on the DEFAULT cache, `beta-notes` on a
    second one, and nothing of `beta-notes` in the default cache. That asymmetry is
    the measured defect, reproduced."""
    default_cache = tmp_path / "cache"
    beta_cache = tmp_path / "cache-beta"
    (default_cache / "alpha-notes").mkdir(parents=True)
    (beta_cache / "beta-notes").mkdir(parents=True)
    # Counts chosen pairwise-distinct and distinct from every constant asserted on,
    # so a mutant that hardcodes a literal cannot survive: 2 here, 3 there.
    for n in range(2):
        (default_cache / "alpha-notes" / f"alpha-{n}.md").write_text("# alpha\n")
    for n in range(3):
        (beta_cache / "beta-notes" / f"beta-{n}.md").write_text("# beta\n")

    binp = tmp_path / "bin"
    binp.mkdir()
    mockbin.write_exec(
        binp / "cairn",
        _CAIRN_STUB.format(default_cache=default_cache, beta_cache=beta_cache),
    )
    mockbin.write_exec(binp / "cairn-validate", _VALIDATE_STUB)
    (tmp_path / "tmp").mkdir()
    return World(
        root=tmp_path, bin=binp, default_cache=default_cache,
        beta_cache=beta_cache, calls=tmp_path / "calls.log",
    )


# =========================================================================== #
# CRITERION 1 — FOUR SCRIPTS, EACH WITH A PRINTED EXIT-CODE VOCABULARY
# =========================================================================== #


class TestTheFourEntryPoints:
    def test_the_directory_holds_exactly_the_four_entry_points(self):
        found = sorted(p.name for p in OPS.glob("*.sh"))
        assert found == sorted(("common.sh", *SCRIPTS)), (
            "the read/write/hygiene/health split was an operator decision.\n"
            f"found: {found}\n"
            "Adding a fifth cluster is a decision, not a drive-by — say so and "
            "revise the split rather than widening this list quietly."
        )

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_help_exits_zero_and_prints_its_codes(self, script, world):
        done = world.run(script, "--help")
        assert done.returncode == 0, f"{script} --help exited {done.returncode}\n{done.stderr}"
        assert "exit codes:" in done.stdout, f"{script} --help printed no exit-code block"
        assert printed_codes(done.stdout), f"{script} --help printed an EMPTY exit-code block"

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_the_printed_codes_are_exactly_the_returnable_codes(self, script, world):
        """🔴 FAILS WHEN THE SET GROWS *OR* SHRINKS. A code the script can return and
        does not print is undocumented; a code it prints and cannot return is a
        promise to a caller that will never see it. Both are the same defect from
        opposite sides, so the assertion is set EQUALITY."""
        src = (OPS / script).read_text(encoding="utf8")
        common = (OPS / "common.sh").read_text(encoding="utf8")

        declared = declared_codes(src, common)
        used_names = used_code_names(src, common)
        unknown = used_names - set(declared)
        assert not unknown, f"{script} exits via undeclared names: {sorted(unknown)}"

        returnable = {declared[n] for n in used_names}
        printed = printed_codes(world.run(script, "--help").stdout)
        assert printed == returnable, (
            f"{script}'s printed exit codes disagree with the codes it can return.\n"
            f"  printed but unreturnable: {sorted(printed - returnable)}\n"
            f"  returnable but unprinted: {sorted(returnable - printed)}\n"
            "Both directions are defects: an unprinted code is undocumented, and a "
            "printed one that cannot happen is a promise to a caller."
        )

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_no_bare_numeric_exit_survives(self, script):
        """A literal `exit 7` cannot be cross-checked against the printed legend, so
        the ledger above would be blind to it. Every exit names a constant."""
        src = (OPS / script).read_text(encoding="utf8")
        bare = _BARE.findall(src)
        assert not bare, f"{script} has bare numeric exit(s): {bare}"

    def test_every_script_declares_the_passthrough(self):
        """The client's own codes reach the caller untranslated, so every `--help`
        has to say so — otherwise the set-equality test above would demand that
        3/4/5/6/7/8/9/11 be printed as though the wrapper owned them."""
        for script in SCRIPTS:
            src = (OPS / script).read_text(encoding="utf8")
            assert "cairn_passthrough_legend" in src, (
                f"{script} does not print the passthrough legend, so a caller has "
                "no way to know an unlisted code is the client's"
            )


class TestTheLedgerParsersCanSee:
    """🔴 POSITIVE CONTROLS ON THE LEDGER'S OWN INSTRUMENT. A reassuring zero from
    either parser is indistinguishable from a parser wired to nothing, so each is
    fed a case it MUST see and the number is watched to move."""

    def test_printed_codes_finds_a_real_block(self):
        text = "blah\nexit codes:\n  0   fine\n  19  no client\n  20  no scope\n"
        assert printed_codes(text) == {0, 19, 20}
        assert printed_codes("no block here at all") == set()

    def test_declared_codes_finds_a_real_declaration(self):
        assert declared_codes("readonly EXIT_WEIRD=77\n") == {"EXIT_WEIRD": 77}
        assert declared_codes("EXIT_WEIRD=77\n") == {}

    def test_the_comparison_can_go_red(self):
        """The mismatch the parametrized test above asserts against, forced."""
        src = 'readonly EXIT_A=31\nreadonly EXIT_B=32\nexit "$EXIT_A"\nexit "$EXIT_B"\n'
        declared = declared_codes(src)
        returnable = {declared[n] for n in used_code_names(src)}
        printed = printed_codes("exit codes:\n  31  documented\n")
        assert printed != returnable, "the comparison cannot distinguish these, so it proves nothing"
        assert sorted(returnable - printed) == [32]

    def test_the_bare_exit_scanner_can_go_red(self):
        assert _BARE.findall("  exit 5\n") == ["5"]
        assert _BARE.findall('  exit "$EXIT_OK"\n') == []


# =========================================================================== #
# CRITERION 2 — A NEGATIVE CONTROL PER SCRIPT, WATCHED REFUSING
#
# 🔴 EACH INPUT IS A MISTAKE SOMEBODY ACTUALLY MAKES, not a textbook fixture:
# `claude/RULES.md` — "Build the bad case from REALISTIC data… scanners allowlist
# their own canonical examples and scan clean."
#
# Each of the four was ALSO watched refusing against this host's real deployment
# on 2026-09-27; those runs are recorded in the commit message. The cases below are
# the hermetic re-statement, so the gate does not need a pod.
# =========================================================================== #


class TestEachScriptRefusesWithItsOwnCode:
    def test_read_refuses_a_read_with_no_resolvable_scope(self, world, tmp_path):
        """The realistic case: `/analyze-service` or an agent runs a recall from a
        scratch directory.

        ⚠ WHAT THIS DOES *NOT* SHOW, corrected after being measured: an earlier
        docstring here said the client's own answer would be an EMPTY report reading
        as "nothing recorded". That is false. The deployed client refuses this shape
        at **rc 2** naming the remedy (`could not derive a scope from '.': … pass
        --scope explicitly`), and its own comment records the older behaviour as rc 1
        plus a traceback — so no version of it ever returned an empty report here.
        What rc 20 buys is a code in this directory's >=19 band, distinguishable from
        the client's 2; it is co-extensive with the client's refusal, not a hole the
        client leaves open. Scope of the claim: `recall` and `search` only — see
        `TestTheScopePreflightCoversOnlyTheVerbsThatNeedAScope` for `ls-entries`,
        which takes no scope and must NOT be gated on one.
        """
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        done = world.run("read.sh", "recall", cwd=outside)
        assert done.returncode == 20, f"rc={done.returncode}\n{done.stderr}"
        assert "no scope could be resolved" in done.stderr

    def test_write_refuses_a_bullet_that_carries_its_own_marker_and_date(self, world):
        """The realistic case: a writer composes the bullet by copying the shape of
        the entry it is appending to. The server adds BOTH the marker and the date,
        so the pod accepts this and renders it double-prefixed — no exit code
        upstream reports it."""
        done = world.run(
            "write.sh", "append", "--scope", "alpha-notes", "--ref", "alpha-0",
            "--session", "11111111-2222-3333-4444-555555555555",
            "--text", "- 2000-01-01: a bullet a human typed",
        )
        assert done.returncode == 21, f"rc={done.returncode}\n{done.stderr}"
        assert "NOTHING WAS SENT" in done.stderr
        assert not world.calls.exists(), (
            "the protocol refusal must precede the client — the call log exists, so "
            "something was sent before the check ran"
        )

    def test_hygiene_refuses_a_check_that_would_check_nothing(self, world):
        """The realistic case: a scope that is routed but holds nothing on its
        instance yet — an agent running the mandated post-write check right after a
        create that was refused. The underlying checker prints an honest banner and
        exits 0; this refuses."""
        # The state is forced the honest way — the routed scope's entry files are
        # REMOVED, which is what a routed-but-unseeded scope really looks like.
        # Nothing is stubbed out of the code path.
        done = world.run("hygiene.sh", "validate", "--scope", "beta-notes",
                         env=self._env_with_empty_beta(world))
        assert done.returncode == 22, f"rc={done.returncode}\n{done.stdout}\n{done.stderr}"
        assert "NOTHING WAS CHECKED" in done.stderr

    @staticmethod
    def _env_with_empty_beta(world: World) -> dict[str, str]:
        """Empty the `beta` cache for one call. Deleting the entry files is the
        state a routed-but-unseeded scope is really in — nothing is stubbed out."""
        for f in (world.beta_cache / "beta-notes").glob("*.md"):
            f.unlink()
        return world.env

    def test_health_refuses_a_scope_the_table_does_not_route(self, world):
        """The realistic case: a plausible near-miss scope name. PR #1872's second
        consequence is that `scope-absent` is per-INSTANCE as well as per-host, so
        this refusal is the one that stops a session concluding "unrecorded"."""
        done = world.run("health.sh", "instances", "--scope", "beta-note")
        assert done.returncode == 23, f"rc={done.returncode}\n{done.stderr}"
        assert "does not route" in done.stderr

    def test_health_refuses_an_alias_that_is_not_configured(self, world):
        done = world.run("health.sh", "instances", "--instance", "gamma")
        assert done.returncode == 23, f"rc={done.returncode}\n{done.stderr}"

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_every_script_refuses_a_missing_client_rather_than_skipping(self, script, tmp_path):
        """🔴 THE SHAPE THE REPLACED RECIPES GOT WRONG. Three skills carried
        `if command -v cairn; then …; else echo "skipped: cairn unavailable"; fi` —
        exit 0 on an absent client. For a mandated check that is a recorded pass over
        nothing."""
        env = dict(os.environ)
        env["PATH"] = path_without_cairn()
        verb = {"read.sh": "recall", "write.sh": "append",
                "hygiene.sh": "validate", "health.sh": "doctor"}[script]
        args = [str(OPS / script), verb, "--scope", "alpha-notes"]
        if script == "write.sh":
            args += ["--ref", "r", "--session", "s", "--text", "a plain bullet"]
        done = subprocess.run(args, capture_output=True, text=True, env=env,
                              cwd=str(tmp_path), timeout=60)
        assert done.returncode == 19, f"{script} rc={done.returncode}\n{done.stderr}"
        assert "not on PATH" in done.stderr

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_if_available_turns_that_refusal_into_a_skip(self, script, tmp_path):
        """The opt-in the preflights need, so the DEFAULT can be a refusal."""
        env = dict(os.environ)
        env["PATH"] = path_without_cairn()
        verb = {"read.sh": "recall", "write.sh": "append",
                "hygiene.sh": "validate", "health.sh": "doctor"}[script]
        args = [str(OPS / script), verb, "--scope", "alpha-notes", "--if-available"]
        if script == "write.sh":
            args += ["--ref", "r", "--session", "s", "--text", "a plain bullet"]
        done = subprocess.run(args, capture_output=True, text=True, env=env,
                              cwd=str(tmp_path), timeout=60)
        assert done.returncode == 0, f"{script} rc={done.returncode}\n{done.stderr}"
        assert "skipped" in done.stderr


class TestTheScopePreflightCoversOnlyTheVerbsThatNeedAScope:
    """🔴 THE PRE-FLIGHT MUST NOT REFUSE WHAT THE CLIENT ACCEPTS.

    `ls-entries` takes no scope: the real client ignores `--scope` for it and lists
    every entry on every instance, which the module docstring above already records
    as MEASURED. An earlier `read.sh` put `ls-entries` in the same `case` arm as
    `recall`, so a listing from a non-repo cwd was refused at rc 20 — a call the
    bare client answers at rc 0.

    MATRIX, measured against the deployed client from three non-repo cwds
    (`/tmp`, a fresh scratch dir, `$HOME`) before the arm was split:

        cairn ls-entries              rc 0, 443 lines
        read.sh ls-entries            rc 20, 0 lines      <- the defect
        read.sh ls-entries --scope X  rc 0, 443 lines      <- byte-identical to bare

    🔴 THE THIRD ROW IS WHY THE SECOND TEST BELOW EXISTS. A guard that a meaningless
    `--scope` value defeats, while changing the output not at all, is walkable as
    well as wrong — so asserting only that the bare call now succeeds would pass
    over a wrapper that still treats that value as meaningful.
    """

    def test_ls_entries_needs_no_scope_and_is_not_refused_without_one(self, world):
        """`world.root` is a tmp_path — not a git repo — so nothing can resolve a
        scope here. That is exactly the shape that was refused."""
        done = world.run("read.sh", "ls-entries")
        assert done.returncode == 0, (
            f"read.sh ls-entries rc={done.returncode} from a non-repo cwd.\n"
            "`ls-entries` takes no scope — the client ignores --scope for it and "
            "lists every instance. Refusing it here blocks a call the bare client "
            f"answers at rc 0.\nstderr: {done.stderr}"
        )
        assert "no scope could be resolved" not in done.stderr
        # The positive control: it did not merely exit 0, it LISTED. 2 alpha + 3
        # beta entries, counts chosen pairwise-distinct by the `world` fixture.
        lines = [ln for ln in done.stdout.splitlines() if ln.startswith("[")]
        assert len(lines) == 5, f"listed {len(lines)} entries, expected 5\n{done.stdout}"

    def test_a_meaningless_scope_value_changes_nothing_about_the_listing(self, world):
        """The walkability control. The client ignores `--scope` for `ls-entries`, so
        the two invocations must agree BYTE FOR BYTE on stdout and on the exit code.
        A wrapper that gates on the flag's presence makes them disagree."""
        bare = world.run("read.sh", "ls-entries")
        flagged = world.run("read.sh", "ls-entries", "--scope", "no-such-scope-at-all")
        assert (bare.returncode, bare.stdout) == (flagged.returncode, flagged.stdout), (
            "`read.sh ls-entries` and the same call carrying a meaningless --scope "
            "disagree, so the wrapper is treating a value the client ignores as "
            "meaningful.\n"
            f"  bare:    rc={bare.returncode} {len(bare.stdout)} B\n"
            f"  --scope: rc={flagged.returncode} {len(flagged.stdout)} B\n"
        )

    def test_recall_and_search_still_refuse_with_no_resolvable_scope(self, world):
        """The other side of the set: narrowing the arm must not disarm the refusal
        for the two verbs that DO derive a scope. Measured co-extensive with the
        client's own rc 2 for both, which is why rc 20 is worth having at all — it
        is a wrapper-owned code in the >=19 band, distinguishable from the client's."""
        for args in (("recall",), ("search", "a query")):
            done = world.run("read.sh", *args)
            assert done.returncode == 20, (
                f"read.sh {args[0]} rc={done.returncode}, expected 20\n{done.stderr}"
            )
            assert "no scope could be resolved" in done.stderr


# =========================================================================== #
# CRITERION 3 — A POSITIVE CONTROL PER SCRIPT, AND THE PAIR IS REPORTED
#
# 🔴 A REASSURING ZERO IS INDISTINGUISHABLE FROM A HARNESS WIRED TO NOTHING, so
# each case below feeds something that MUST produce a non-zero count, prints
# "N on the positive control, 0 under test", and asserts BOTH halves.
#
# 🔴 THREE OF THE FOUR TARGET THE NON-DEFAULT INSTANCE, because that is the case
# that produced the real wrong reading: the default cache is walked, zero files are
# checked, and the exit code is still 0.
# =========================================================================== #


def path_without_cairn() -> str:
    """The ambient PATH with every directory that holds a `cairn` removed.

    🔴 NOT `PATH=<tmp>`, WHICH WAS MEASURED WRONG. Emptying PATH removes `bash`
    too, so `#!/usr/bin/env bash` fails with **127** and `env: 'bash': No such file
    or directory` — a refusal that looks like the guard firing and is not it. The
    absent-client state has to be built by removing exactly the client."""
    keep = [
        d for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and not (Path(d) / "cairn").exists()
    ]
    assert keep, "no PATH entry survived the filter, so this control tests nothing"
    return os.pathsep.join(keep)


def report_pair(label: str, positive: int, control: int) -> None:
    """Print the pair criterion 3 asks for. Visible under `pytest -s`.

    ⚠ NO `capsys` IN ANY CALLER'S SIGNATURE. Requesting that fixture re-enables
    capture for that test even under `-s`, so three of these four pairs were
    silently swallowed while one printed — a reassuring partial report, which is the
    shape this whole convention exists to avoid."""
    print(f"[{label}] {positive} on the positive control, {control} under test")


class TestEachScriptCanObserve:
    def test_read_search_counts_hunks_on_the_NON_DEFAULT_instance(self, world):
        hit = world.run("read.sh", "search", "beta", "--scope", "beta-notes")
        miss = world.run("read.sh", "search", "zzznope", "--scope", "beta-notes")
        n = sum(len(re.findall(r"— (\d+) of \d+ hunks", ln)) for ln in hit.stdout.splitlines())
        hunks = int(re.search(r"— (\d+) of \d+ hunks", hit.stdout).group(1))
        misses = len(re.findall(r"— (\d+) of \d+ hunks", miss.stdout))
        report_pair("read.sh search / beta-notes (NON-DEFAULT instance)", hunks, misses)
        assert hit.returncode == 0 and n == 1
        assert hunks > 0, "the positive control produced no hunks, so this harness observes nothing"
        assert misses == 0

    def test_write_counts_the_protocol_violations_it_refuses(self, world):
        """The count that must move is REFUSALS, measured over four realistic bad
        texts against one well-formed one."""
        bad = [
            "- a bullet a human typed",
            "2000-01-01: a bullet copied off the entry above",
            "line one\nline two",
            "x" * 2100,
        ]
        base = ["append", "--scope", "alpha-notes", "--ref", "alpha-0",
                "--session", "11111111-2222-3333-4444-555555555555"]
        caught = sum(
            1 for t in bad
            if world.run("write.sh", *base, "--text", t).returncode == 21
        )
        good = world.run("write.sh", *base, "--text", "a plain one-line bullet",
                         "--no-verify")
        false_refusals = 1 if good.returncode == 21 else 0
        report_pair("write.sh append protocol", caught, false_refusals)
        assert caught == len(bad), f"only {caught} of {len(bad)} realistic bad texts were refused"
        assert false_refusals == 0, "a well-formed bullet was refused as a protocol violation"

    def test_hygiene_checks_files_on_the_NON_DEFAULT_instance(self, world):
        """🔴 THE MEASURED WRONG READING, REPRODUCED AND THEN CLOSED. Under test is
        the checker pointed at the DEFAULT cache for a scope that lives on `beta`:
        it counts nothing and exits 0. The positive control is the same check through
        `hygiene.sh`, which resolves the store for the SCOPE."""
        naked = subprocess.run(
            [str(world.bin / "cairn-validate"), "--scope", "beta-notes",
             "--store", str(world.default_cache)],
            capture_output=True, text=True, timeout=60,
        )
        under_test = len(re.findall(r"checked: (\d+) entry file", naked.stdout))
        assert naked.returncode == 0, (
            "the fixture must reproduce the defect: the naked checker exits 0 on a "
            "scope it found nothing for"
        )

        done = world.run("hygiene.sh", "validate", "--scope", "beta-notes")
        hit = re.search(r"checked: (\d+) entry file", done.stdout)
        positive = int(hit.group(1)) if hit else 0
        report_pair("hygiene.sh validate / beta-notes (NON-DEFAULT instance)",
                    positive, under_test)
        assert done.returncode == 0, f"rc={done.returncode}\n{done.stdout}\n{done.stderr}"
        assert positive == 3, (
            "the positive control must check the three files the world put on the "
            f"beta instance; it checked {positive}"
        )
        assert under_test == 0, (
            "the control must check NOTHING — if it checked something the fixture is "
            "not reproducing the per-instance defect at all"
        )

    def test_health_instances_counts_rows_including_the_NON_DEFAULT_one(self, world):
        done = world.run("health.sh", "instances")
        rows = [ln for ln in done.stdout.splitlines() if "\t" in ln]
        missing = world.run("health.sh", "instances", "--instance", "gamma")
        control = len([ln for ln in missing.stdout.splitlines() if "\t" in ln])
        report_pair("health.sh instances", len(rows), control)
        assert done.returncode == 0, done.stderr
        assert len(rows) == 2, f"expected both instances, got {rows}"
        assert f"beta\t{world.beta_cache}" in done.stdout, (
            "the non-default instance's cache root is the answer nothing else on "
            "this host printed in one place"
        )
        assert control == 0


class TestTheCheckSyncsFirst:
    """🔴 THE PROPERTY `test_cairn_skill_verb_ledger.py` USED TO PIN IN PROSE, NOW
    PINNED IN BEHAVIOUR — and it is here because that ledger FOUND THE DEFECT.

    The mandated post-write check was spelled `cairn sync && cairn-validate --scope
    <scope>`, and the `cairn sync` half is load-bearing: `append` writes to the POD
    and does not touch the local cache, so an unsynced check cleanly parses the
    PRE-WRITE bytes and passes on exactly the defect it exists to catch. The first
    draft of `hygiene.sh` did NOT sync — it relied on `write.sh` having synced a
    moment earlier, which is true for a write through that door and FALSE for a human
    validating after a manual `cairn append`. Removing the ledger row is what exposed
    it.

    🔴 THESE READ THE STUB CLIENT'S CALL LOG, NOT THE SOURCE. A grep for `cairn sync`
    in the script would pass with the call sitting in a branch that never runs — the
    reassuring-zero shape one level down. The log records every invocation in order,
    so the assertion is that a `sync` for THIS scope precedes the check."""

    @staticmethod
    def _calls(world: World) -> list[str]:
        return world.calls.read_text().splitlines() if world.calls.exists() else []

    def test_validate_syncs_the_scope_before_it_checks(self, world):
        done = world.run("hygiene.sh", "validate", "--scope", "beta-notes")
        assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
        calls = self._calls(world)
        synced = [i for i, c in enumerate(calls) if c.startswith("sync ") and "beta-notes" in c]
        assert synced, (
            "the check ran without syncing — it would have parsed the PRE-WRITE "
            f"bytes. calls: {calls}"
        )
        reads = [i for i, c in enumerate(calls) if c.startswith(("recall ", "ls-entries"))]
        assert reads and min(reads) > min(synced), (
            f"the sync must come FIRST; call order was {calls}"
        )

    def test_no_sync_suppresses_it_and_nothing_else(self, world):
        """The positive control on the flag: the same run with `--no-sync` must show
        ZERO syncs and still produce the same verdict, so the count moving is the
        flag's doing and not the harness's."""
        done = world.run("hygiene.sh", "validate", "--scope", "beta-notes", "--no-sync")
        calls = self._calls(world)
        syncs = [c for c in calls if c.startswith("sync ")]
        assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
        assert syncs == [], f"--no-sync still synced: {calls}"
        print(f"[hygiene.sh sync] 1 on the positive control, {len(syncs)} under test")

    def test_a_failed_sync_refuses_rather_than_checking_stale_bytes(self, world):
        """Forced: a client whose `sync` fails. The check must NOT proceed — a pass
        over pre-write bytes is the silent green this whole door exists to remove."""
        stub = (world.bin / "cairn").read_text()
        (world.bin / "cairn").write_text(
            stub.replace("  sync) exit 0 ;;", "  sync) echo 'cairn: refresh failed' >&2; exit 4 ;;")
        )
        done = world.run("hygiene.sh", "validate", "--scope", "beta-notes")
        assert done.returncode == 4, f"rc={done.returncode}\n{done.stdout}\n{done.stderr}"
        assert "was NOT refreshed" in done.stderr
        assert "checked:" not in done.stdout, (
            "the checker ran anyway, over bytes the sync failed to refresh"
        )

    def test_write_verifies_through_that_same_door(self, world):
        """🔴 ONE RULE, ONE PLACE — the seam guard. `write.sh` must not carry its own
        copy of the sync: the property has to hold for a caller that never touches
        `write.sh` at all."""
        src = (OPS / "write.sh").read_text(encoding="utf8")
        assert '"$HERE/hygiene.sh" validate' in src, (
            "write.sh no longer delegates its post-write check to hygiene.sh"
        )
        assert "cairn sync" not in src.split("# ---")[-1], (
            "write.sh appears to run its own `cairn sync` again — that is the second "
            "copy, and it leaves the property false for every other caller"
        )
        done = world.run(
            "write.sh", "append", "--scope", "beta-notes", "--ref", "beta-0",
            "--session", "11111111-2222-3333-4444-555555555555",
            "--text", "a plain one-line bullet",
        )
        assert done.returncode == 0, f"rc={done.returncode}\n{done.stderr}"
        calls = self._calls(world)
        assert any(c.startswith("append ") for c in calls), calls
        assert any(c.startswith("sync ") for c in calls), (
            f"the write was not followed by a synced check: {calls}"
        )


class TestTheParsedFormatsAreStillThere:
    """🔴 PARSING THE CLIENT'S OUTPUT MAKES ITS FORMAT A DEPENDENCY, so each pattern
    is asserted against the shape the stub reproduces AND against a non-match.
    `claude/RULES.md`: "no matches" means "possibly the wrong pattern", not "nothing
    there"."""

    def test_the_store_line_is_what_resolution_reads(self, world):
        done = world.run("health.sh", "instances", "--scope", "beta-notes")
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == f"beta\t{world.beta_cache}\tbeta-notes"

    def test_a_missing_reader_resolution_row_refuses_rather_than_printing_a_blank(
            self, world, tmp_path):
        """The format-drift case, forced: a `doctor --json` with no
        `<alias>/reader-resolution` row must produce a refusal, never a row with an
        empty second column."""
        mockbin.write_exec(
            world.bin / "cairn",
            'case "$1" in\n'
            '  routes) echo "instances: personal, beta";'
            ' echo "  beta-notes -> beta"; exit 0 ;;\n'
            '  doctor) echo \'{"checks":[]}\'; exit 10 ;;\n'
            '  *) exit 2 ;;\n'
            'esac\n',
        )
        done = world.run("health.sh", "instances")
        assert done.returncode == 23, f"rc={done.returncode}\n{done.stdout}"
        assert "\t" not in done.stdout, f"a blank row was printed: {done.stdout!r}"


# =========================================================================== #
# CRITERION 4 — THE SKILLS CARRY NO INVOCATION THE SCRIPTS NOW OWN
# =========================================================================== #

#: The verbs `scripts/cairn-ops/` took ownership of. `doctor` and `sync` are here
#: too: `health.sh` owns them.
OWNED_VERBS = frozenset(
    {"sync", "recall", "search", "ls-entries", "validate", "append", "put", "create",
     "doctor"}
)

#: 🔴 THE ALLOWLIST, AND WHY EACH ROW IS ON IT. The `cairn` skill IS the surface
#: documentation — it exists to state what the client's verbs are and what their
#: exit codes mean, so a guard that stripped the verbs from it would delete the
#: only place the surface is written down. Nothing else is allowlisted.
INVOCATION_ALLOWLIST: dict[str, str] = {
    "cairn/SKILL.md": (
        "the surface itself: the verb table, the `doctor` block and the two exit-4s. "
        "A router that cannot name the verbs it routes to documents nothing."
    ),
    "cairn/reference/operator-surface.md": (
        "the OPERATOR reference — pod, seed, cutover, the create-into-an-absent-scope "
        "reproduction. Named explicitly in this task's acceptance criteria."
    ),
}

#: 🔴 WHAT THIS GUARD COVERS, AT THE WIDTH OF ITS IMPLEMENTATION RATHER THAN OF ITS
#: TITLE. It scans FENCED code blocks only — the lines an agent copies and runs.
#: Prose that NAMES a verb is deliberately out of scope: the reasoning about the
#: surface is the most valuable content in these files, and a guard that banned the
#: word would force the reasoning out. So this is a guard on EXECUTABLE RECIPES, and
#: a skill may still explain `cairn append`'s contract in a sentence.
#: ⚠ That means a recipe written OUTSIDE a fence is invisible here. Two of the
#: migrated files (`prune-index/reference/writing-and-safety.md`,
#: `analyze-service/reference/write-back.md`) carried exactly that shape, which is
#: why the call-site ledger below pins them by CONTENT instead of relying on this.
_FENCE = re.compile(r"^\s*(`{3,})")


def fenced_lines(text: str) -> list[tuple[int, str]]:
    """Every line inside a fenced block, with its 1-based line number.

    Handles the 4-backtick nesting `claude/skills/handoff/SKILL.md` uses: a fence is
    closed only by a run of at least as many backticks, so a ```bash block nested
    inside a ````markdown block is INSIDE, not outside. A naive toggle reads that
    file's recipe as prose."""
    out: list[tuple[int, str]] = []
    stack: list[int] = []
    for i, line in enumerate(text.splitlines(), start=1):
        hit = _FENCE.match(line)
        if hit:
            n = len(hit.group(1))
            if stack and n >= stack[-1]:
                stack.pop()
                continue
            stack.append(n)
            continue
        if stack:
            out.append((i, line))
    return out


#: `cairn <word>` or `cairn-validate <word>`, with the leading look-behind that
#: stops `scripts/cairn-validate` and `.../cairn recall` in a PATH from matching —
#: those are the pointer spellings, not the invocation being migrated away from.
_INVOKE = re.compile(r"(?<![\w/.-])cairn(-validate)?\s+(-{0,2}[a-z][a-z-]*)")


def owned_invocations(text: str) -> list[tuple[int, str]]:
    """Fenced lines that INVOKE a verb `scripts/cairn-ops/` owns."""
    out: list[tuple[int, str]] = []
    for lineno, line in fenced_lines(text):
        for hit in _INVOKE.finditer(line):
            if hit.group(1) == "-validate" or hit.group(2) in OWNED_VERBS:
                out.append((lineno, line.strip()))
                break
    return out


def skill_md_files() -> list[Path]:
    """Every `.md` under `claude/skills/`.

    🔴 NO `git ls-files`. `nix flake check` builds the hermetic tier from a
    tracked-file copy with NO `.git`, so an unguarded `git` call exits 128 and reds
    that tier while the pre-push tier stays green — the two-tier blind spot
    `test_conditional_skip_pins.py` records."""
    return sorted(SKILLS.rglob("*.md"))


class TestSkillBodiesCarryNoOwnedInvocation:
    def test_the_population_is_not_empty(self):
        """A vacuity floor. Every assertion below is satisfied by an empty tree."""
        files = skill_md_files()
        assert len(files) >= 60, f"only {len(files)} skill .md files found — the scan is looking in the wrong place"

    def test_the_fence_parser_can_see(self):
        """Positive control, including the 4-backtick nesting that a naive toggle
        gets backwards."""
        text = "prose\n```bash\ncairn sync\n```\nmore\n"
        assert fenced_lines(text) == [(3, "cairn sync")]
        nested = "````markdown\n# doc\n```bash\ncairn recall --repo X\n```\n````\n"
        inside = [ln for _, ln in fenced_lines(nested)]
        assert "cairn recall --repo X" in inside, (
            "a ```bash block nested inside a ````markdown block must read as FENCED"
        )
        assert owned_invocations("`cairn sync` is the remedy\n") == [], (
            "prose is deliberately out of scope — see the guard's own comment"
        )
        assert owned_invocations("```\ncairn sync\n```\n"), (
            "the scanner must be able to MATCH, or its zero means nothing"
        )

    def test_no_unallowlisted_skill_fences_an_owned_invocation(self):
        offenders: dict[str, list[tuple[int, str]]] = {}
        for path in skill_md_files():
            rel = path.relative_to(SKILLS).as_posix()
            if rel in INVOCATION_ALLOWLIST:
                continue
            hits = owned_invocations(path.read_text(encoding="utf8"))
            if hits:
                offenders[rel] = hits
        assert not offenders, (
            "these skills still fence a cairn invocation that `scripts/cairn-ops/` "
            "now owns — replace it with a pointer:\n"
            + "\n".join(f"  {k}:{n}  {t}" for k, v in offenders.items() for n, t in v)
            + "\nThe allowlist is INVOCATION_ALLOWLIST in this file, and every row "
            "there carries its reason."
        )

    def test_the_allowlist_names_files_that_exist_and_still_need_it(self):
        """🔴 FAILS WHEN THE ALLOWLIST GROWS *OR* SHRINKS INTO IRRELEVANCE. A row for
        a file that no longer fences anything is a stale exemption, and a stale
        exemption is what makes the next real one invisible."""
        for rel, reason in INVOCATION_ALLOWLIST.items():
            path = SKILLS / rel
            assert path.exists(), f"allowlisted file {rel} does not exist"
            assert len(reason.strip()) >= 40, f"{rel}'s exemption reason is too short to be a reason"
            assert owned_invocations(path.read_text(encoding="utf8")), (
                f"{rel} is allowlisted but fences no owned invocation — the row is "
                "stale, and a stale exemption hides the next real one"
            )


# =========================================================================== #
# CRITERION 5 — THE CALL-SITE LEDGER: OLD RECIPE GONE, POINTER PRESENT
# =========================================================================== #

#: 🔴 THE MIGRATED SET, AND IT FAILS WHEN IT GROWS *OR* SHRINKS. Each key is a skill
#: file that carried an inline cairn recipe before this change; each value is
#: (a substring of the recipe that must be GONE, the pointer that must be PRESENT).
#:
#: A row disappearing is the recipe coming back by another name; a file appearing
#: with a recipe and no row is the migration silently stopping. Both are the same
#: regression from opposite sides, so the key set is asserted, not iterated.
MIGRATED: dict[str, tuple[str, str]] = {
    "subsystem-index/SKILL.md": (
        "cairn sync && cairn-validate --scope",
        "$DEVRC/scripts/cairn-ops/hygiene.sh validate --scope",
    ),
    "prune-index/SKILL.md": (
        "cairn sync && S=",
        "$DEVRC/scripts/cairn-ops/hygiene.sh audit --scope",
    ),
    "clawgate/SKILL.md": (
        'if command -v cairn >/dev/null; then cairn search',
        "$DEVRC/scripts/cairn-ops/read.sh search",
    ),
    "clawgate/reference/prior-work-recall.md": (
        'if command -v cairn >/dev/null; then cairn search',
        "$DEVRC/scripts/cairn-ops/read.sh search",
    ),
    "obs-read/SKILL.md": (
        'if command -v cairn >/dev/null; then cairn search',
        "$DEVRC/scripts/cairn-ops/read.sh search",
    ),
    "analyze-service/SKILL.md": (
        "cairn sync; python3",
        "$DEVRC/scripts/cairn-ops/health.sh sync",
    ),
    # 🔴 NOT A RECIPE ROW — A PROSE ROW, AND IT IS ON THIS LEDGER BECAUSE THE LEDGER
    # FOUND IT. Its "Location:" bullet named the FROZEN mirror as the store's
    # location; the pointer replaced it with the per-instance resolution. Criterion 6
    # is what this row enforces on the `/analyze-service` side.
    # 🔴 THE OTHER DOOR. `write-back.md` is `/analyze-service`'s write-back door and
    # `subsystem-index/SKILL.md` is the one protocol; they were genuinely FORKED once
    # (closed by operator decision 2026-08-31) and `test_index_append_protocol.py`
    # compares them. Migrating one door's spelling and not the other reopens exactly
    # that fork, which is why this row exists rather than a note.
    "analyze-service/reference/write-back.md": (
        "`cairn create --scope <scope> --ref <slug> --file <scratch>` for a first-ever",
        "$DEVRC/scripts/cairn-ops/write.sh create --scope <scope> --ref <slug>",
    ),
    "analyze-service/reference/index-store.md": (
        "**Location:** `~/.claude/analyze-service-index/<scope>/<slug>.md` — local",
        "$DEVRC/scripts/cairn-ops/health.sh instances",
    ),
    "resume/SKILL.md": (
        'cairn recall --repo "<path>"',
        "$DEVRC/scripts/cairn-ops/read.sh recall --repo",
    ),
    "resume/reference/handoff-search.md": (
        'cairn recall --repo "<path>"',
        "$DEVRC/scripts/cairn-ops/read.sh recall --repo",
    ),
    "handoff/SKILL.md": (
        "cairn recall --repo <path>",
        "$DEVRC/scripts/cairn-ops/read.sh recall --repo",
    ),
}


#: The three THIN ROUTING SKILLS. Each delegates to one script and carries no
#: recipe, so a pointer in one of them is not a migrated call site — it is the
#: router doing its job. Excluded from the ledger population for that reason, and
#: pinned two ways of their own below.
ROUTERS: dict[str, str] = {
    "cairn-read": "read.sh",
    "cairn-write": "write.sh",
    "cairn-hygiene": "hygiene.sh",
}

#: 🔴 FILES THAT POINT AT THE LAYER WITHOUT BEING A MIGRATED CALL SITE. A `MIGRATED`
#: row asserts a recipe is GONE; this file's recipes deliberately STAY — it is the
#: surface documentation, which is why it is the only entry in
#: `INVOCATION_ALLOWLIST` too. It also now names the doors, so that the raw table and
#: the skills' routing do not read as two equal choices: the two doors disagreeing on
#: `ls-entries` is what let a wrapper refuse a listing the bare verb answers.
#: A row here is an EXEMPTION, and an exemption is auditable where an absence is the
#: defect — so it is pinned both ways below, exactly like `INVOCATION_ALLOWLIST`.
POINTS_WITHOUT_MIGRATING: dict[str, str] = {
    "cairn/SKILL.md": (
        "the surface documentation: it keeps the raw verb table on purpose and names "
        "the doors beside it, so it points at the layer without any recipe moving."
    ),
}


class TestTheThinRouters:
    @pytest.mark.parametrize("skill", sorted(ROUTERS))
    def test_the_router_exists_and_names_its_script(self, skill):
        path = SKILLS / skill / "SKILL.md"
        assert path.exists(), f"the {skill} routing skill does not exist"
        text = path.read_text(encoding="utf8")
        assert f"scripts/cairn-ops/{ROUTERS[skill]}" in text, (
            f"{skill} does not name scripts/cairn-ops/{ROUTERS[skill]}"
        )

    @pytest.mark.parametrize("skill", sorted(ROUTERS))
    def test_the_router_carries_no_duplicated_recipe(self, skill):
        """🔴 A ROUTER THAT RESTATES ITS SCRIPT'S RECIPE IS THE SECOND COPY. The
        whole argument for this layer is that a predicate open-coded at N sites is
        wrong at N−1 of them, so a router fencing a raw `cairn <verb>` would
        reintroduce the thing it replaced."""
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf8")
        hits = owned_invocations(text)
        assert not hits, f"{skill} fences a raw cairn invocation: {hits}"

    @pytest.mark.parametrize("skill", sorted(ROUTERS))
    def test_the_router_points_at_where_the_decisions_live(self, skill):
        """A router must hand off rather than absorb: each names the skill that owns
        the judgement it does not make."""
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf8")
        owner = {"cairn-read": "cairn", "cairn-write": "subsystem-index",
                 "cairn-hygiene": "prune-index"}[skill]
        assert owner in text, f"{skill} does not point at the {owner} skill"


class TestTheCallSiteLedger:
    @pytest.mark.parametrize("rel", sorted(MIGRATED))
    def test_the_old_recipe_is_gone(self, rel):
        gone, _ = MIGRATED[rel]
        text = (SKILLS / rel).read_text(encoding="utf8")
        assert gone not in text, (
            f"{rel} still carries the pre-migration recipe {gone!r}. Recipes become "
            "POINTERS, never deletions — but the recipe itself must go, or the two "
            "spellings drift exactly as `cairn sync && cairn-validate` already did."
        )

    @pytest.mark.parametrize("rel", sorted(MIGRATED))
    def test_the_pointer_is_present(self, rel):
        _, pointer = MIGRATED[rel]
        text = (SKILLS / rel).read_text(encoding="utf8")
        assert pointer in text, (
            f"{rel} lost its pointer {pointer!r}. A deletion breaks the flow this "
            "migration exists to preserve."
        )

    def test_the_ledger_covers_every_file_that_had_one(self):
        """🔴 GROW *OR* SHRINK, both ways. The population is "skill files that
        reference the cairn-ops layer at all": every one of them must be ledgered,
        and every ledger row must name a file that points at the layer."""
        pointing = {
            p.relative_to(SKILLS).as_posix()
            for p in skill_md_files()
            if "scripts/cairn-ops/" in p.read_text(encoding="utf8")
            and p.relative_to(SKILLS).parts[0] not in ROUTERS
            and p.relative_to(SKILLS).as_posix() not in POINTS_WITHOUT_MIGRATING
        }
        ledgered = set(MIGRATED)
        assert pointing == ledgered, (
            "the set of skills pointing at `scripts/cairn-ops/` is not the ledger:\n"
            f"  pointing, not ledgered: {sorted(pointing - ledgered)}\n"
            f"  ledgered, not pointing: {sorted(ledgered - pointing)}\n"
            "A row that vanishes is the recipe coming back; a file that appears "
            "without a row is the migration stopping quietly."
        )

    @pytest.mark.parametrize("rel", sorted(POINTS_WITHOUT_MIGRATING))
    def test_the_non_migrating_exemption_is_not_stale(self, rel):
        """🔴 FAILS IF THE EXEMPTION STOPS EARNING ITSELF, either way. A row for a
        file that no longer points at the layer is a stale exemption, and a stale
        exemption is what makes the next real one invisible — the same ruling
        `test_the_allowlist_names_files_that_exist_and_still_need_it` applies to
        `INVOCATION_ALLOWLIST`.

        🔴 AND IT MUST NOT BE ON BOTH LEDGERS. `MIGRATED` asserts a recipe is GONE;
        this set says no recipe moved. A file on both would have the two guards
        asserting opposite things about it, and whichever ran first would look right.
        """
        path = SKILLS / rel
        assert path.exists(), f"exempted file {rel} does not exist"
        reason = POINTS_WITHOUT_MIGRATING[rel]
        assert len(reason.strip()) >= 40, f"{rel}'s exemption reason is too short to be a reason"
        assert "scripts/cairn-ops/" in path.read_text(encoding="utf8"), (
            f"{rel} is exempted from the call-site ledger but no longer points at "
            "`scripts/cairn-ops/` at all — drop the row rather than leaving it"
        )
        assert rel not in MIGRATED, (
            f"{rel} is on BOTH the migrated ledger and the non-migrating exemption. "
            "Those make contradictory claims about the same file; pick one."
        )

    def test_every_pointer_names_a_script_that_exists(self):
        for rel, (_, pointer) in MIGRATED.items():
            hit = re.search(r"scripts/cairn-ops/([\w.-]+\.sh)", pointer)
            assert hit, f"{rel}'s pointer names no script: {pointer!r}"
            assert (OPS / hit.group(1)).exists(), (
                f"{rel} points at scripts/cairn-ops/{hit.group(1)}, which does not exist"
            )


# =========================================================================== #
# CRITERION 6 — THE STORE IS ONE READ-THROUGH CACHE PER CONFIGURED INSTANCE
# =========================================================================== #

# The frozen pre-cutover mirror. Naming it is FINE; naming it as though it were the
# store is the defect — cg#563 measured that `subsystem_touch.py` still defaults to
# it while `service_recon.py` resolves the live cache correctly.
#: 🔴 THE POPULATION IS THE PATH, NOT THE STRING — AND THE FIRST DRAFT OF THIS GUARD
#: WAS WRONG ABOUT THAT IN THE EXPENSIVE DIRECTION. Matching the bare token
#: `analyze-service-index` flagged thirteen lines, of which the majority named a
#: systemd UNIT (`analyze-service-index-backup.service`), a backup BUCKET or a handoff
#: DOC — none of them a claim about where the store is. A guard whose population is
#: mostly false positives is one somebody switches off.
FROZEN = ".claude/analyze-service-index"

#: A mention is QUALIFIED when the line, or either line touching it, says something
#: that makes the path NOT the live store. Each of these is a substantive statement,
#: not a hedge: `0444` and "nothing refreshes" are the measured facts about the freeze.
FROZEN_QUALIFIERS = (
    "frozen", "mirror", "563", "pre-cutover", "0444", "nothing refreshes",
)

#: The prose rule's two subjects. cg#563's documentation half is exactly these.
STORE_PROSE_SKILLS = ("prune-index", "subsystem-index")


def qualified(line: str) -> bool:
    low = line.lower()
    return any(q in low for q in FROZEN_QUALIFIERS)


class TestTheStoreIsPerInstance:
    def test_the_grep_can_match(self):
        """🔴 POSITIVE CONTROL FIRST. A zero from an unqualified-mention scan is
        indistinguishable from a scan wired to nothing."""
        assert not qualified(f"the store lives at ~/.claude/{FROZEN}")
        assert qualified(f"~/.claude/{FROZEN} is the frozen pre-cutover mirror")
        assert qualified(f"~/.claude/{FROZEN} (cg#563 owns the code default)")

    def test_the_grep_reads_ONE_LINE_OF_CONTEXT_EITHER_SIDE(self):
        """A second positive control, on the CONTEXT half. The qualification routinely
        sits on the next line, because the path ends a sentence that the following
        line finishes — `cairn/SKILL.md` is exactly that shape."""
        lines = [f"the cache; ~/{FROZEN}", "is the pre-cutover mirror, frozen."]
        assert not qualified(lines[0])
        assert qualified(lines[1])

    def test_no_skill_presents_the_frozen_mirror_as_the_store(self):
        offenders: list[str] = []
        for path in skill_md_files():
            lines = path.read_text(encoding="utf8").splitlines()
            for i, line in enumerate(lines, 1):
                if FROZEN not in line:
                    continue
                window = lines[max(0, i - 2):i + 1]
                if any(qualified(w) for w in window):
                    continue
                offenders.append(f"{path.relative_to(SKILLS).as_posix()}:{i}  {line.strip()}")
        assert not offenders, (
            "these lines name the FROZEN pre-cutover mirror without saying it is "
            "frozen — a reader takes it for the store:\n  " + "\n  ".join(offenders)
            + "\ncg#563 owns the code default; this task owns the prose."
        )

    @pytest.mark.parametrize("skill", STORE_PROSE_SKILLS)
    def test_the_store_is_described_per_instance(self, skill):
        """The claim, pinned as a claim rather than as a keyword: each of these two
        skills must say the store is one read-through cache PER CONFIGURED INSTANCE,
        and must not present a single cache path as "the store"."""
        text = "\n".join(
            p.read_text(encoding="utf8") for p in sorted((SKILLS / skill).rglob("*.md"))
        )
        # Whitespace-normalised: the claim is a SENTENCE, and a line wrap inside it
        # is not a different claim. A raw substring check made the guard depend on
        # where the paragraph happened to break.
        flat = " ".join(text.lower().split())
        assert "per configured instance" in flat, (
            f"{skill} does not describe the store as one read-through cache per "
            "configured instance. Measured on this host 2026-09-27: TWO caches plus "
            "the frozen mirror — three trees. devrc PR #1872 carries the measurement."
        )

    @pytest.mark.parametrize("skill", STORE_PROSE_SKILLS)
    def test_no_single_cache_path_is_called_the_store(self, skill):
        """A named cache path immediately followed by a definite-article claim is the
        shape that undercounts. The pattern is deliberately narrow and its positive
        control is beside it."""
        bad = re.compile(r"~/\.cache/subsystem-store\b(?![-\w])[^\n]{0,40}\bis the (?:store|index store)\b")
        assert bad.search("~/.cache/subsystem-store is the store"), (
            "the pattern cannot match its own target, so its zero means nothing"
        )
        for path in sorted((SKILLS / skill).rglob("*.md")):
            text = path.read_text(encoding="utf8")
            hit = bad.search(text)
            assert not hit, (
                f"{path.relative_to(SKILLS).as_posix()} calls one cache path THE "
                f"store: {hit.group(0)!r}. There is one per configured instance."
            )
