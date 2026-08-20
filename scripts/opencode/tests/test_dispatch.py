r"""Tests for `opencode-dispatch` — the stage-1 opencode dispatch gate.

WHAT IS UNDER TEST, and why each one exists (every claim below is a MEASURED
failure, not a hypothetical):

  1. THE CANONICAL ARGV ORDER. `--file` is an ARRAY option and greedily
     swallowed the message as a second filename (`Error: File not found:
     Execute the task described in the attached brief…`). Only
     message-positional-FIRST worked. Pinned by INDEX, not by "the message is
     in there somewhere".

  2. PATH CONTAINMENT — the hard block. `external_directory: "ask"` +
     `opencode run`'s auto-reject = exit 0 having done nothing, twice. Both
     directions are pinned: it REFUSES an out-of-dir path (rc 3) and it ACCEPTS
     the in-dir control (rc 0). A refuser that refuses everything is not a gate.

  3. POSITIVE CONTROLS on both scanners. `extract_paths` and `scan_commands`
     each get a fixture that MUST produce a non-zero count, so a reassuring
     "0 external paths / 0 warnings" can never mean "wired to nothing".
     RULES.md 🔴: report the pair, never the zero alone.

  4. NO FOREGROUND MODE. 20% of opencode sessions exceed the Bash tool's hard
     600,000 ms ceiling; one dispatch died at exactly 600s with `Exit code 143`.
     Asserted structurally (the parser has no such flag) AND behaviourally
     (the spawn is `start_new_session=True`, detached from the caller's process
     group, which is what a tool timeout kills).

  5. THE SEAM. preflight's permission verdicts must come from the SAME resolver
     `scripts/tests/test_opencode_config.py` pins with ~500 assertions. Asserted
     by object identity, because "verified in isolation" is how a defect lives
     in the seam nobody owns.

  6. THE TELEMETRY LEDGER. Every outcome string the CLI can emit must appear in
     adoption-scan's REGISTRY row, and vice versa — otherwise the adoption
     report silently buckets a real outcome as "unknown".

  7. GIT TRACKING. 🔴 A new file that is not `git add`ed is silently omitted
     from the flake, and the switch SUCCEEDS with the file simply absent.

    run:  python -m pytest scripts/opencode/tests/test_dispatch.py -q
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
OC_DIR = ROOT / "scripts" / "opencode"
CLI_PATH = OC_DIR / "opencode-dispatch"
SKILL_PATH = OC_DIR / "SKILL.md"
HOME_NIX = ROOT / "nix" / "home.nix"
ADOPTION = ROOT / "scripts" / "session-analysis" / "adoption-scan.py"

sys.path.insert(0, str(OC_DIR / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import brief_scan  # noqa: E402
import oc_permissions  # noqa: E402


# 🔴 A `/usr/…` path used as TEST DATA — an argument to a pure string predicate,
# never an interpreter and never written to a file.
#
# It deliberately does NOT spell `/usr/bin/env`. `scripts/tests/
# test_runtime_shebangs.py` scans every `test_*.py` for that literal and flagged
# the earlier spelling here. Two routes were open and this is the chosen one:
#
#   * ALLOWLIST the hit — CLOSED by that file's own 🔴: "Nothing here may be
#     `/usr/bin/env`". Taking it would mean changing the rule, not satisfying it,
#     and its docstring names adding an entry to go green as the anti-pattern.
#   * RESTRUCTURE — taken. The assertion never needed that specific string: it
#     proves "an enumerated /usr prefix returns a reason", which ANY /usr path
#     demonstrates. Narrowing my own fixture is strictly cheaper and lower-risk
#     than widening a repo-wide gate.
#
# The scanner IS over-broad in the narrow sense that it cannot tell a path being
# *mentioned* from one being *written as a shebang* — but making it able to
# would mean parsing Python, which is the trap guard_core's DESIGN NOTE records
# (three rounds of a regex "is this inert text?" helper, each with a fresh hole).
# A fixed-string gate that fails closed for one retype is the better trade, and
# it is the same stance guard_core takes about quoted commit messages.
ALLOWLISTED_USR_PATH = "/usr/share/doc/example/readme"


def _load_cli():
    """Import the extensionless executable as a module."""
    spec = importlib.util.spec_from_loader(
        "opencode_dispatch",
        importlib.machinery.SourceFileLoader("opencode_dispatch", str(CLI_PATH)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load_cli()


# --------------------------------------------------------------------------- #
# 1. the canonical argv order
# --------------------------------------------------------------------------- #
MESSAGE = "Read .opencode-dispatch/x.md first"


def test_message_is_the_positional_at_the_pinned_index():
    argv = D.build_argv(MESSAGE, "/some/dir")
    assert argv[:2] == ["opencode", "run"]
    assert argv.index(MESSAGE) == D.MESSAGE_ARGV_INDEX == 2, (
        "the message must be the FIRST positional. Measured: with the message "
        "after `--file`, opencode consumed it as a second filename and died "
        f"with `Error: File not found: <message>`. Got argv={argv}"
    )


def test_message_precedes_every_file_flag():
    argv = D.build_argv(MESSAGE, "/d", files=["/d/a.md", "/d/b.md"])
    assert argv.index(MESSAGE) < argv.index("--file")
    assert argv.count("--file") == 2


def test_the_flags_that_actually_recur_are_all_emitted():
    # --dir 18 / --title 7 / --file 7 / -m 6 / --auto 4 / -c 2 over n=24.
    argv = D.build_argv(MESSAGE, "/d", model="m/x", title="T", auto=True,
                        continue_session=True, files=["/d/a.md"])
    for pair in (["--dir", "/d"], ["--title", "T"], ["-m", "m/x"],
                 ["--file", "/d/a.md"]):
        i = argv.index(pair[0])
        assert argv[i:i + 2] == pair
    assert "--auto" in argv and "-c" in argv


def test_no_model_flag_when_no_alias_is_given():
    # 18 of 24 measured dispatches passed no -m at all; the config default must
    # survive, so the flag has to be ABSENT rather than filled in here.
    assert "-m" not in D.build_argv(MESSAGE, "/d")


@pytest.mark.parametrize("alias,expected", [
    ("flash", "openrouter/deepseek/deepseek-v4-flash"),
    ("mimo", "openrouter/xiaomi/mimo-v2.5"),
    ("pro", "openrouter/deepseek/deepseek-v4-pro"),
])
def test_model_aliases_resolve(alias, expected):
    assert D.resolve_model(alias) == expected


def test_a_full_model_id_passes_through():
    assert D.resolve_model("openrouter/vendor/thing") == "openrouter/vendor/thing"


def test_an_unknown_bare_alias_is_a_usage_error_not_a_passthrough():
    with pytest.raises(ValueError):
        D.resolve_model("flsah")


# --------------------------------------------------------------------------- #
# 2 + 3. path containment, both directions, with positive controls
# --------------------------------------------------------------------------- #
def test_extract_paths_positive_control():
    """🔴 The scanner MUST be able to produce a non-zero count.

    Without this, every "0 external paths" below is indistinguishable from a
    scanner wired to nothing.
    """
    found = brief_scan.extract_paths(
        "Read /opt/example/input.txt and also ~/notes/todo.md before starting."
    )
    assert "/opt/example/input.txt" in found
    assert "~/notes/todo.md" in found
    assert len(found) == 2


def test_extract_paths_does_not_fire_on_prose_or_urls():
    """The negative half of the same control: a scanner that matches everything
    reports a confident non-zero on any text and is equally useless."""
    text = ("Weigh read/write costs at 20/80 and glob **/*.py; "
            "the docs are at https://example.invalid/a/b/c.")
    assert brief_scan.extract_paths(text) == []


@pytest.mark.parametrize("url,leaked", [
    ("https://example.invalid/a?f=/etc/passwd", "/etc/passwd"),
    ("https://example.invalid/a#/opt/thing", "/opt/thing"),
    ("see https://example.invalid/q?path=~/notes.md now", "~/notes.md"),
])
def test_the_url_strip_is_load_bearing_for_query_and_fragment_paths(url, leaked):
    """🔴 Found by a SURVIVING mutant: deleting the URL strip passed the whole
    suite, because the lookbehind alone already rejects `https://host/a/b` —
    every `/` there is preceded by an excluded character.

    A path in a QUERY or FRAGMENT is different: it is preceded by `=` or `#`,
    which are NOT in the excluded class, so it IS extracted. Without the strip a
    brief that merely LINKS to documentation would be refused, which is the
    permanently-red-gate failure this whole design is trying to avoid.

    The `leaked` column is the positive control: it names what the regex
    genuinely can see, so "extract_paths returned []" here cannot mean "the
    scanner is wired to nothing".
    """
    assert brief_scan._PATH_RE.search(url).group(1) == leaked
    assert brief_scan.extract_paths(url) == []


def test_is_under_does_not_treat_a_sibling_prefix_as_a_child():
    assert brief_scan.is_under("/w/repo/x", "/w/repo") is True
    assert brief_scan.is_under("/w/repo", "/w/repo") is True
    assert brief_scan.is_under("/w/repo-other/x", "/w/repo") is False


def test_scan_paths_refuses_a_path_outside_dir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    outside = tmp_path / "elsewhere" / "brief.md"
    offenders = brief_scan.scan_paths(f"Start from {outside}.", d)
    assert [o.text for o in offenders] == [str(outside)]
    assert offenders[0].kind == "brief"


# --------------------------------------------------------------------------- #
# 2b. the three "claims wider than what it checked" findings
# --------------------------------------------------------------------------- #
def test_a_relative_path_with_a_dotdot_segment_escapes_and_is_caught(tmp_path):
    """🔴 opencode runs with `cwd=--dir`, so `../outside/x` genuinely leaves it —
    and SKILL.md used to tell the brief's author to name locations relative to
    `--dir`, which invited exactly this. Measured before the fix:
    `paths examined : 0` plus an unconditional all-clear."""
    d = tmp_path / "proj"
    d.mkdir()
    (tmp_path / "outside").mkdir()
    offenders = brief_scan.scan_paths("Read ../outside/extra.md and apply it.", d)
    assert [o.text for o in offenders] == ["../outside/extra.md"]


def test_a_relative_path_that_stays_inside_is_not_an_offender(tmp_path):
    """The in-dir control for the rule above. `a/../b` resolves back inside, so
    a blanket 'any `..` is an offender' rule would be wrong."""
    d = tmp_path / "proj"
    d.mkdir()
    assert brief_scan.scan_paths("Edit sub/../src/x.py then stop.", d) == []


@pytest.mark.parametrize("prose", [
    "compare v1..v2 for the delta",
    "run git log origin/main..HEAD",
    "and then... it stops",
    "the range HEAD~2..HEAD is what changed",
])
def test_the_dotdot_scanner_does_not_fire_on_prose_or_git_ranges(prose, tmp_path):
    """The negative control. A `..` between two words is not a path, and a
    scanner that thinks it is blocks every brief mentioning a git range."""
    d = tmp_path / "proj"
    d.mkdir()
    assert brief_scan.scan_paths(prose, d) == []


@pytest.mark.parametrize("token", [
    "${pkgs.python312}/bin/python3",
    "${DEVRC}/scripts/tests",
    "$DEVRC/scripts/tests",
    "{base}/sub/file.py",
])
def test_a_variable_built_path_is_UNMEASURED_never_blocked_and_never_clean(token, tmp_path):
    """🔴 Both spellings were wrong, in OPPOSITE directions, and both are the
    likeliest content of a brief written in this repo.

    `${X}/y` FALSE-BLOCKED (the `}` is not in the lookbehind's excluded class,
    so `/y` was read as an absolute path); `$X/y` SILENTLY PASSED (the `/`
    follows a letter, which is excluded) — and CLAUDE.md tells agents to use the
    `$DEVRC`/`$HOMELAB` handles. With no override flag, a false block leaves
    'reword or abandon the tool'.
    """
    d = tmp_path / "proj"
    d.mkdir()
    text = f"Run the thing at {token} when ready."
    assert brief_scan.scan_paths(text, d) == [], "must not BLOCK an unresolvable path"
    assert brief_scan.extract_paths(text) == [], "must not count it as examined"
    assert brief_scan.extract_unresolved_paths(text) == [token], \
        "must be reported UNMEASURED, not silently dropped"


def test_an_attachment_outside_dir_is_an_offender(tmp_path):
    """🔴 `--file` is the #3 most-used flag and SKILL.md advertises it. Measured
    before this: `run --dir <proj> --file <outside>/extra.md` printed
    'external paths : none — every path is under --dir' and handed opencode the
    outside file."""
    d = tmp_path / "proj"
    d.mkdir()
    outside = tmp_path / "elsewhere" / "extra.md"
    offenders = brief_scan.scan_attachments([str(outside)], d)
    assert [(o.text, o.kind) for o in offenders] == [(str(outside), "attachment")]


def test_an_attachment_inside_dir_is_accepted(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    assert brief_scan.scan_attachments([str(d / "notes.md")], d) == []


def test_the_system_allowlist_is_judged_lexically_not_by_realpath(tmp_path):
    """🔴 The comment claimed `/etc` and `/var` are deliberately absent. On NixOS
    `/etc/hosts` realpaths into `/nix/store/…-hosts`, hit the `/nix/store` entry
    and passed — so every `/etc` path the list claimed to block was silently
    allowed. Under-blocking, invisible, and the comment asserted the opposite."""
    d = tmp_path / "proj"
    d.mkdir()
    assert brief_scan.is_allowlisted("/etc/hosts") is None
    assert brief_scan.is_allowlisted(ALLOWLISTED_USR_PATH) is not None
    assert [o.text for o in brief_scan.scan_paths("edit /etc/hosts", d)] == \
        ["/etc/hosts"]


def test_scan_paths_accepts_the_in_dir_control(tmp_path):
    d = tmp_path / "proj"
    (d / "sub").mkdir(parents=True)
    inside = d / "sub" / "thing.py"
    assert brief_scan.scan_paths(f"Edit {inside} and re-run.", d) == []


def test_scan_paths_ignores_the_enumerated_system_prefixes(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    assert brief_scan.scan_paths(
        f"pipe it to /dev/null and read {ALLOWLISTED_USR_PATH}", d) == []


def test_tmp_is_deliberately_not_allowlisted(tmp_path):
    """🔴 Claude's scratchpad lives under /tmp and 'the brief lived in the
    scratchpad' is failure 1's exact vector."""
    assert not any(p.startswith("/tmp") for p in brief_scan.SYSTEM_ALLOW)
    d = tmp_path / "proj"
    d.mkdir()
    assert brief_scan.scan_paths("see /tmp/claude-1000/scratchpad/brief.md", d)


def _run_cli(args, stdin_text):
    """Drive the real CLI end to end, as a subprocess, over stdin."""
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        input=stdin_text, capture_output=True, text=True, timeout=120,
    )


def test_preflight_exits_with_the_distinct_rc_on_an_external_path(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    outside = tmp_path / "other" / "spec.md"
    p = _run_cli(["preflight", "--dir", str(d)], f"Implement what {outside} says.")
    assert p.returncode == D.RC_PATH_ESCAPE == 3, p.stdout + p.stderr
    assert str(outside) in p.stdout


def test_preflight_accepts_the_in_dir_control(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "spec.md").write_text("x")
    p = _run_cli(["preflight", "--dir", str(d)],
                 f"Implement what {d / 'spec.md'} says.")
    assert p.returncode == D.RC_OK, p.stdout + p.stderr


def test_preflight_json_reports_examined_beside_found(tmp_path):
    import json
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d), "--json"],
                 f"Touch {d / 'a'} and /opt/elsewhere/b")
    rep = json.loads(p.stdout)
    assert rep["paths_examined"] == 2
    assert len(rep["external_paths"]) == 1


# --------------------------------------------------------------------------- #
# 2c. 🔴 THE PRINTED CLAIM. Pinned as WHOLE NORMALISED LINES, not substrings —
#     a guard on words is walkable by rewording, and these sentences ARE the
#     tool's central claim about what it checked.
# --------------------------------------------------------------------------- #
def _lines(out: str) -> list[str]:
    return [ln.rstrip() for ln in out.split("\n")]


# 🔴 THE LITERAL SENTENCES, WRITTEN OUT HERE — deliberately NOT imported from
# the CLI. A mutation sweep caught the earlier version doing
# `D.VERDICT_CLEAN.format(n=2) in lines`: mutating the constant mutated the
# expectation with it, so a reworded claim SURVIVED a fully green suite. That is
# RULES.md's "never derive a test's expectation from the implementation it
# tests", in the one place where the implementation IS a claim about itself.
#
# A cosmetic reword now fails this file. That is the price of a machine-readable
# claim and it is worth paying.
LINE_NOT_EXAMINED = ("  external paths    : NOT EXAMINED — no resolvable path "
                     "in the brief, and no attachments")
LINE_CLEAN_2 = "  external paths    : none — all 2 examined resolve under --dir"


def test_the_verdict_constants_match_the_sentences_pinned_here():
    """The two-way pin. The literals above are the source of truth; this asserts
    the CLI still emits exactly them, so the other tests can use either."""
    assert D.VERDICT_NOT_EXAMINED == LINE_NOT_EXAMINED
    assert D.VERDICT_CLEAN.format(n=2) == LINE_CLEAN_2


def test_an_empty_scan_says_NOT_EXAMINED_not_an_all_clear(tmp_path):
    """🔴 THE finding. The report used to print
    'external paths : none — every path is under --dir' UNCONDITIONALLY, right
    beside 'paths examined : 0'. "I found nothing" and "there was nothing to
    find" are different claims."""
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d)], "Just tidy the code up.")
    lines = _lines(p.stdout)
    assert LINE_NOT_EXAMINED in lines, p.stdout
    assert "  paths examined       : 0" in lines
    assert "  attachments examined : 0" in lines
    # …and the clean sentence must be ABSENT, in every arity it can take.
    assert not any(ln.startswith("  external paths    : none") for ln in lines)
    assert p.returncode == D.RC_OK


def test_a_real_clean_scan_says_how_many_it_examined(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.md").write_text("x")
    p = _run_cli(["preflight", "--dir", str(d), "--file", str(d / "a.md")],
                 f"Edit {d / 'src.py'} please.")
    lines = _lines(p.stdout)
    assert LINE_CLEAN_2 in lines, p.stdout
    assert LINE_NOT_EXAMINED not in lines
    assert p.returncode == D.RC_OK


def test_the_report_never_claims_containment_for_an_unchecked_attachment(tmp_path):
    """The end-to-end shape of finding 1: an out-of-dir attachment must BLOCK,
    and the clean sentence must not appear anywhere in the output."""
    d = tmp_path / "proj"
    d.mkdir()
    outside = tmp_path / "elsewhere" / "extra.md"
    outside.parent.mkdir()
    outside.write_text("x")
    p = _run_cli(["run", "--dir", str(d), "--file", str(outside)], "do a thing")
    lines = _lines(p.stdout)
    assert p.returncode == D.RC_PATH_ESCAPE, p.stdout + p.stderr
    assert not any(ln.startswith("  external paths    : none") for ln in lines)
    assert LINE_NOT_EXAMINED not in lines
    assert f"     [attachment] {outside}" in lines
    assert "NOT DISPATCHED." in lines


def test_unresolved_paths_are_reported_separately_from_both_verdicts(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d)], "run ${DEVRC}/scripts/tests now")
    lines = _lines(p.stdout)
    assert p.returncode == D.RC_OK
    # Not blocked, not counted as clean, and named.
    assert LINE_NOT_EXAMINED in lines
    assert "     ${DEVRC}/scripts/tests" in lines
    assert any(ln.startswith("  UNMEASURED paths     : 1") for ln in lines)


def test_the_warn_header_names_the_mechanism_that_applies(tmp_path):
    """The header asserted "`opencode run` AUTO-REJECTS an `ask`" even when every
    row was a `deny` — a claim about a mechanism that did not fire."""
    d = tmp_path / "proj"
    d.mkdir()
    deny_only = "```bash\ngit stash push -m wip\n```\n"
    p = _run_cli(["preflight", "--dir", str(d)], deny_only)
    assert "[deny]" in p.stdout, p.stdout
    assert "AUTO-REJECTS an `ask`" not in p.stdout
    ask_case = "```bash\nkubectl -n d exec p -- sh\n```\n"
    q = _run_cli(["preflight", "--dir", str(d)], ask_case)
    assert "AUTO-REJECTS an `ask`" in q.stdout


# --------------------------------------------------------------------------- #
# 2d. an EMPTY brief must never dispatch
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["", "\n", "   \n\t\n"])
def test_an_empty_brief_is_refused_and_never_dispatches(text, tmp_path):
    """🔴 Measured before this check: a 0-byte brief returned rc 0, printed
    DISPATCHED and emitted outcome=dispatched. A dropped heredoc is the single
    most likely operator error, and this tool exists to kill
    exit-0-having-done-nothing — it was manufacturing a fresh instance."""
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["run", "--dir", str(d)], text)
    assert p.returncode == D.RC_USAGE == 2, p.stdout + p.stderr
    assert "DISPATCHED" not in p.stdout
    assert "EMPTY" in p.stderr
    assert not (d / D.BRIEF_SUBDIR).exists(), "an empty brief was still installed"


def test_a_missing_brief_file_lands_in_the_documented_rc_vocabulary(tmp_path):
    """It raised an uncaught FileNotFoundError -> rc 1, a code this tool never
    documents, and emitted NO telemetry — so adoption-scan's `error` bucket
    undercounted the failure an operator hits most."""
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d), "--brief",
                  str(tmp_path / "nope.md")], "")
    assert p.returncode == D.RC_USAGE, p.stdout + p.stderr
    assert "Traceback" not in p.stderr
    assert "cannot read --brief" in p.stderr


def test_the_brief_error_path_emits_telemetry(tmp_path, monkeypatch):
    """The other half: rc alone is not the fix — the `error` bucket has to see it."""
    seen = []
    monkeypatch.setattr(D, "emit", lambda o, *a, **k: seen.append(o))
    monkeypatch.setattr(D.sys, "stdin", _Stdin(""))
    d = tmp_path / "proj"
    d.mkdir()
    assert D.main(["run", "--dir", str(d)]) == D.RC_USAGE
    assert seen == ["error"]


# --------------------------------------------------------------------------- #
# 3b. the command scanner's positive control
# --------------------------------------------------------------------------- #
KUBECTL_BRIEF = """\
Do the migration.

```bash
kubectl -n data exec deploy/pg -- psql -c 'select 1'
```
"""


def test_scan_commands_positive_control_names_the_glob(tmp_path):
    """🔴 The measured failure-3 command, and the rule it trips.

    `kubectl exec … psql` was auto-rejected mid-run and the dispatch abandoned.
    A warning that cannot name the glob is not actionable.
    """
    warnings = brief_scan.scan_commands(KUBECTL_BRIEF, tmp_path)
    asks = [w for w in warnings if w.action == "ask"]
    assert asks, "the ask scanner produced ZERO on a brief that must produce one"
    assert any(w.pattern == "*kubectl*exec*" for w in asks), [w.pattern for w in asks]


def test_the_guard_core_channel_positive_control(tmp_path):
    """🔴 The channel used to sit behind a bare `except: guard_reason = None`,
    and a mutant making `evaluate` raise SURVIVED all 717 tests — a broken
    channel and a clean brief printed identically. `evaluate` raises on an
    unknown policy name BY DESIGN, so renaming the "opencode" policy is exactly
    the shape that would have gone dark forever."""
    brief = "```bash\nrm -rf /\n```\n"
    reasons = [w.guard_reason for w in brief_scan.scan_commands(brief, tmp_path)]
    assert any(r for r in reasons), \
        "positive control: guard_core produced ZERO verdicts on `rm -rf /`"
    assert not any(r.startswith(brief_scan.GUARD_UNMEASURED) for r in reasons if r)


def test_a_broken_guard_core_channel_reports_COULD_NOT_MEASURE(tmp_path, monkeypatch):
    """…and when it genuinely cannot judge, it says so instead of returning the
    same None a clean brief produces."""
    def boom(*a, **k):
        raise TypeError("evaluate() got an unexpected keyword argument 'cwd'")
    monkeypatch.setattr(brief_scan.guard_core, "evaluate", boom)
    verdicts = brief_scan.scan_commands("```bash\nrm -rf /\n```\n", tmp_path)
    assert verdicts, "a broken guard channel silently produced no rows at all"
    assert all(v.guard_reason.startswith(brief_scan.GUARD_UNMEASURED)
               for v in verdicts)


def test_scan_commands_is_silent_on_a_read_only_brief(tmp_path):
    clean = "Look around.\n\n```bash\ngit -C . status\nls -la\n```\n"
    assert brief_scan.scan_commands(clean, tmp_path) == []


def test_scan_commands_ignores_a_non_shell_fence(tmp_path):
    py = "```python\nkubectl_exec = 1\n```\n"
    assert brief_scan.scan_commands(py, tmp_path) == []


def test_the_command_scanner_uses_guard_cores_splitter_not_its_own():
    """🔴 One parser. A second splitter is how the two disagree — invisibly,
    since both would still print warnings."""
    src = Path(brief_scan.__file__).read_text()
    assert "import guard_core" in src
    assert "guard_core.split_commands" in src
    # …and no home-grown substitute alongside it. 🔴 The paren is load-bearing:
    # a bare `"re.split"` substring test matched `guard_co(re.split)_commands`
    # and failed on the correct code — a spelled guard catching its own subject.
    assert "re.split(" not in src, "a second command splitter has appeared"


def test_the_splitter_really_reaches_a_wrapped_command(tmp_path):
    """Behavioural proof of the line above: a `VAR=… sudo …` prefix and a `&&`
    chain are exactly what a hand-rolled splitter gets wrong."""
    brief = ("```bash\n"
             "cd /x && KUBECONFIG=$KC_HOMELAB kubectl -n ns exec pod -- sh\n"
             "```\n")
    assert any(w.pattern == "*kubectl*exec*"
               for w in brief_scan.scan_commands(brief, tmp_path))


# --------------------------------------------------------------------------- #
# 4. never in the foreground
# --------------------------------------------------------------------------- #
def test_the_parser_offers_no_foreground_escape_hatch():
    help_text = D.build_parser().format_help()
    run_help = D.build_parser().parse_args  # keep the parser built
    assert run_help is not None
    for banned in ("--foreground", "--fg", "--wait", "--sync", "--blocking"):
        assert banned not in help_text, (
            f"{banned} would reintroduce failure 4: 20% of opencode sessions "
            "exceed the Bash tool's hard 600,000 ms ceiling."
        )


def test_the_dispatch_is_detached_into_its_own_session(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    seen = {}

    class FakeProc:
        pid = 4242

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return FakeProc()

    monkeypatch.setattr(D.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(D.sys, "stdin", _Stdin("do the thing"))
    rc = D.main(["run", "--dir", str(d), "--title", "T", "-m", "flash"])
    assert rc == D.RC_OK
    assert seen["kw"]["start_new_session"] is True, (
        "without its own session the run dies with the calling shell — which is "
        "exactly what a Bash-tool timeout kills (measured: Exit code 143 at 600s)"
    )
    assert seen["kw"]["stdin"] is D.subprocess.DEVNULL
    assert seen["argv"][D.MESSAGE_ARGV_INDEX].startswith("Read .opencode-dispatch/")
    assert "openrouter/deepseek/deepseek-v4-flash" in seen["argv"]


class _Stdin:
    def __init__(self, text):
        self._t = text

    def read(self):
        return self._t


def test_run_does_not_dispatch_when_preflight_blocks(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    called = []
    monkeypatch.setattr(D.subprocess, "Popen",
                        lambda *a, **k: called.append(a) or (_ for _ in ()).throw(
                            AssertionError("dispatched despite a blocked preflight")))
    monkeypatch.setattr(D.sys, "stdin", _Stdin("use /opt/elsewhere/spec.md"))
    assert D.main(["run", "--dir", str(d)]) == D.RC_PATH_ESCAPE
    assert called == []


def test_the_brief_is_written_inside_dir_and_self_ignores(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    monkeypatch.setattr(D.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"pid": 1})())
    monkeypatch.setattr(D.sys, "stdin", _Stdin("a clean brief"))
    assert D.main(["run", "--dir", str(d)]) == D.RC_OK
    sub = d / D.BRIEF_SUBDIR
    briefs = list(sub.glob("*.md"))
    assert len(briefs) == 1
    assert briefs[0].read_text() == "a clean brief"
    # 🔴 Never the scratchpad, and never stageable in whatever repo it lands in.
    assert (sub / ".gitignore").read_text().rstrip().endswith("*")


# --------------------------------------------------------------------------- #
# 5. the seam
# --------------------------------------------------------------------------- #
def test_preflight_resolves_through_the_same_object_the_config_suite_pins():
    """🔴 "Verified in isolation" is the new vacuous green. Both surfaces must
    load ONE resolver, asserted by identity — a matrix that merely AGREES today
    is what a duplicate looks like right up until it does not."""
    import test_opencode_config as cfg
    assert cfg.wildcard_match is oc_permissions.wildcard_match
    assert cfg._resolve_over is oc_permissions.resolve
    assert cfg.strip_jsonc is oc_permissions.strip_jsonc


def test_the_base_ruleset_prepends_the_builtin_catch_all():
    """🔴 The fixture must be able to DISAGREE with the constant.

    The previous version asserted `rules[0] == ("*", "allow")` against the real
    config — whose own first bash key IS `"*": "allow"` — so the fixture could
    only ever produce the value the assertion named, and a mutant dropping the
    built-in prepend SURVIVED. Feed it a config whose first key cannot equal the
    constant, and the prepend becomes observable.
    """
    synthetic = {"permission": {"bash": {"*never*": "deny", "ls*": "allow"}}}
    rules = oc_permissions.base_bash_rules(synthetic)
    assert rules[0] == ("*", "allow"), "the built-in catch-all is not prepended"
    assert rules[1] == ("*never*", "deny"), "config order is not preserved"
    assert len(rules) == 3
    # …and without the prepend, an unmatched command would fall through to
    # opencode's `ask` fallback instead of `allow`. That is the behaviour the
    # constant exists for, so assert it rather than the tuple alone.
    assert oc_permissions.resolve(rules, "echo hi") == "allow"
    assert oc_permissions.resolve(rules[1:], "echo hi") == "ask"


def test_the_base_ruleset_reads_the_real_config_file():
    assert ("*kubectl*exec*", "ask") in oc_permissions.base_bash_rules()


# --------------------------------------------------------------------------- #
# 6. the telemetry ledger
# --------------------------------------------------------------------------- #
REGISTRY_ID = "opencode-dispatch"


def _registry_row() -> dict:
    src = ADOPTION.read_text()
    m = re.search(r'\{\s*"id":\s*"opencode-dispatch".*?\n    \}', src, re.S)
    assert m, "adoption-scan REGISTRY has no opencode-dispatch row"
    return m.group(0)


def test_the_registry_row_declares_the_tool_correctly():
    row = _registry_row()
    assert '"via": "tool"' in row
    assert '"tool": "opencode-dispatch"' in row
    assert '"opt_in": True' in row


def test_every_emitted_outcome_is_declared_in_the_registry():
    """THREE-WAY, both directions. An undeclared outcome is silently bucketed as
    "unknown" in the adoption report; a declared-but-unreachable one makes the
    report claim coverage that cannot exist."""
    emitted = set(re.findall(r'\bemit\(\s*"([a-z-]+)"', CLI_PATH.read_text()))
    assert emitted, "positive control: found ZERO emit() call sites"
    declared = set(re.findall(r'"([a-z-]+)"', re.search(
        r'"outcomes":\s*\[([^\]]*)\]', _registry_row()).group(1)))
    assert emitted == declared == set(D.OUTCOMES), (
        f"emitted={sorted(emitted)} declared={sorted(declared)} "
        f"OUTCOMES={sorted(D.OUTCOMES)}")


def test_no_call_site_passes_a_computed_outcome():
    """🔴 The ledger above is a GREP over literal call sites, so a ternary or a
    variable would be INVISIBLE to it — which is exactly what happened while
    this was being written: `emit(a if x else b, …)` scored as one outcome and
    the second was never seen. Pin the shape the grep can read."""
    src = CLI_PATH.read_text()
    # Statement-position calls only, so prose and comments that merely MENTION
    # the call cannot be mistaken for one (they did, on the first draft).
    call_lines = [ln.strip() for ln in src.split("\n")
                  if ln.strip().startswith("emit(")]
    assert len(call_lines) >= len(D.OUTCOMES), (
        f"positive control: only {len(call_lines)} emit() statements found")
    bad = [ln for ln in call_lines if not re.match(r'^emit\(\s*"', ln)]
    assert bad == [], f"emit() called with a computed outcome: {bad}"
    # …and the ledger's own regex must see EVERY one of them.
    assert len(re.findall(r'^\s*emit\(\s*"', src, re.M)) == len(call_lines)


def test_telemetry_never_changes_the_exit_code(monkeypatch, tmp_path):
    """The invocation contract's hard promise, exercised through THIS caller."""
    def boom(*a, **k):
        raise RuntimeError("spool exploded")
    monkeypatch.setattr(D, "emit", D.emit)          # keep the real wrapper
    monkeypatch.setitem(sys.modules, "invocation",
                        type("M", (), {"emit_invocation": staticmethod(boom)}))
    d = tmp_path / "proj"
    d.mkdir()
    monkeypatch.setattr(D.sys, "stdin", _Stdin("clean"))
    assert D.main(["preflight", "--dir", str(d)]) == D.RC_OK


# --------------------------------------------------------------------------- #
# 7. deployment: git-tracked + declared in home.nix
# --------------------------------------------------------------------------- #
SHIPPED_FILES = [
    "scripts/opencode/SKILL.md",
    "scripts/opencode/opencode-dispatch",
    "scripts/opencode/lib/oc_permissions.py",
    "scripts/opencode/lib/brief_scan.py",
    "scripts/opencode/tests/test_dispatch.py",
]

# 🔴 THE TWO TIERS RUN THIS FILE IN DIFFERENT TREES, AND ONLY ONE HAS A `.git`.
# `nix build .#checks.x86_64-linux.pytests` builds from `/build/src`, a copy with
# no git dir at all — so `git ls-files` exits **128** ("not a git repository"),
# and an assertion that reads that as "not tracked" turns a required gate
# permanently red on a message that is FALSE. Measured on the integration tree:
# five failures, all reporting a tracking problem that did not exist.
def git_dir_present(root: Path) -> bool:
    """Which tier are we in? A FUNCTION of the tree, not a constant.

    🔴 Written as a helper so both answers are reachable from either tier. As a
    bare module-level `(ROOT / ".git").exists()` the obvious pin —
    `assert GIT_DIR_PRESENT == (ROOT / ".git").exists()` — is a fixture that can
    only ever produce the value the assertion names, so a mutant hardcoding it
    to `True` passes on a dev host and the tier switch goes unpinned exactly
    where it matters.

    🔴 `.git` is a FILE, not a directory, inside a git worktree (it holds
    `gitdir: …`), and this repo is developed in worktrees. Hence `.exists()`
    rather than `.is_dir()`: the latter would report "no git" in every worktree
    and silently disable the tracking half on the tier that has it.
    """
    return (root / ".git").exists()


# 🔴 There is deliberately NO `GIT_DIR_PRESENT = git_dir_present(ROOT)` module
# constant. One existed and a mutation sweep hardcoded it to `True`, which
# SURVIVED — on a dev host the probe returns True anyway, so any assertion about
# its value is a fixture that can only produce the constant it names. Removing
# the constant removes the thing that can be hardcoded; every caller computes
# the tier from the tree it is actually looking at.
#
# The use-site wiring is verified by running this whole file in a `.git`-free
# copy of the tree (the sandbox tier reproduced faithfully) — no unit test on a
# dev host can distinguish a correct flag from a hardcoded `True`, because both
# tiers agree there whenever the files really are tracked.


def file_ship_problems(rel: str, root: Path, git_present: bool) -> list[str]:
    """Reasons `rel` would be silently absent from the flake. Empty == fine.

    🔴 TWO INDEPENDENT CHECKS, because neither alone covers both tiers, and the
    git one is made CONDITIONAL rather than skipped outright so it cannot go
    quietly vacuous on the tier that does have a git dir:

      * EXISTENCE is what means something INSIDE the nix sandbox. The store copy
        is built from tracked files only, so an untracked file would simply not
        be there — which this check sees, and `git` could not.
      * TRACKEDNESS is what means something on a DEV HOST, where the file exists
        on disk whether or not git has ever heard of it. That is the actual
        hazard: the switch succeeds and the file is not deployed.

    `git_present` is a PARAMETER rather than a module-level read so both branches
    are exercisable from either tier — see the three control tests below.
    """
    problems = []
    if not (root / rel).is_file():
        problems.append(f"{rel} is missing from this tree")
        return problems          # trackedness of an absent file is not a claim
    if not git_present:
        return problems          # nix sandbox tier: no .git, nothing more to check
    p = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
                       capture_output=True, text=True)
    if p.returncode != 0:
        problems.append(f"{rel} is not git-tracked — `git add` it")
    return problems


@pytest.mark.parametrize("rel", SHIPPED_FILES)
def test_every_new_file_ships(rel):
    """🔴 A new file that is not `git add`ed is silently omitted from the flake.
    The switch SUCCEEDS and the file is simply not there."""
    assert file_ship_problems(rel, ROOT, git_dir_present(ROOT)) == []


def test_the_tier_probe_answers_BOTH_ways_from_either_tier(tmp_path):
    """🔴 The pin on the tier switch, built so it cannot agree with itself.

    Both fixtures are constructed here, so this runs identically in the nix
    sandbox and on a dev host, and a mutant hardcoding the probe to either
    constant dies in one of the two arms.
    """
    (tmp_path / "with").mkdir()
    (tmp_path / "with" / ".git").write_text("gitdir: /elsewhere\n")   # worktree shape
    (tmp_path / "without").mkdir()
    assert git_dir_present(tmp_path / "with") is True
    assert git_dir_present(tmp_path / "without") is False


# --- the three controls on the tier guard ---------------------------------- #
# 🔴 These build their own fixtures, so they run identically in BOTH tiers —
# which is the point: a guard that can only be exercised on a dev host is a
# guard nobody re-checks after the sandbox breaks it.
def test_a_missing_file_is_reported_in_either_tier(tmp_path):
    """Positive control. Without this, every `== []` above is indistinguishable
    from a helper wired to nothing."""
    assert file_ship_problems("nope.md", tmp_path, False) == \
        ["nope.md is missing from this tree"]
    assert file_ship_problems("nope.md", tmp_path, True) == \
        ["nope.md is missing from this tree"]


def test_a_DIRECTORY_at_the_path_is_not_a_shipped_file(tmp_path):
    """🔴 Found by a SURVIVING mutant: weakening `is_file()` to `exists()` passed
    the whole suite, because nothing ever put a non-file at a shipped path.

    The claim these entries make is "this FILE ships". A directory standing where
    `SKILL.md` should be satisfies `exists()` and would be reported clean while
    the deploy has no skill — the same silent-absence failure, one level in.
    """
    (tmp_path / "SKILL.md").mkdir()
    assert file_ship_problems("SKILL.md", tmp_path, False) == \
        ["SKILL.md is missing from this tree"]


def test_an_untracked_file_is_reported_when_git_is_present(tmp_path):
    """The check the sandbox CANNOT make, made here against a real git repo.

    This is what proves the dev-host tier still catches the real hazard — the
    thing the `.git` guard must not have thrown away.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True,
                   capture_output=True)
    (tmp_path / "tracked.md").write_text("x")
    (tmp_path / "untracked.md").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.md"], check=True,
                   capture_output=True)
    assert file_ship_problems("tracked.md", tmp_path, True) == []
    assert file_ship_problems("untracked.md", tmp_path, True) == \
        ["untracked.md is not git-tracked — `git add` it"]


def test_a_present_file_in_a_git_free_tree_reports_NOTHING(tmp_path):
    """🔴 THE REGRESSION CASE, and it fails in an ORDINARY checkout if the
    `.git` guard is ever deleted.

    It reproduces the sandbox tier exactly — a real file in a tree with no git
    dir — so `git ls-files` would exit 128 here just as it does at `/build/src`.
    With the guard: no problems. Without it: `128 != 0`, and the suite goes red
    on a dev host instead of only in CI, which is where this defect hid.
    """
    (tmp_path / "shipped.md").write_text("x")
    assert not (tmp_path / ".git").exists()
    assert file_ship_problems("shipped.md", tmp_path, False) == []


def test_the_tier_guard_short_circuits_before_invoking_git(tmp_path, monkeypatch):
    """…and it must not merely SWALLOW git's failure — it must not call git at
    all. A helper that ran git and ignored rc 128 would pass the test above
    while still paying for, and depending on, a binary the sandbox may not have.
    """
    def explode(*a, **k):
        raise AssertionError("git was invoked despite git_present=False")
    monkeypatch.setattr(subprocess, "run", explode)
    (tmp_path / "shipped.md").write_text("x")
    assert file_ship_problems("shipped.md", tmp_path, False) == []


def test_home_nix_deploys_the_skill_as_an_out_of_store_symlink():
    src = HOME_NIX.read_text()
    for target in ('".claude/skills/opencode/SKILL.md"',
                   '".claude/skills/opencode/opencode-dispatch"',
                   '".local/bin/opencode-dispatch"'):
        assert target in src, f"home.nix does not deploy {target}"
    block = src[src.index('".claude/skills/opencode/SKILL.md"'):]
    assert "mkOutOfStoreSymlink" in block[:400], (
        "the executable must be an mkOutOfStoreSymlink like browser/dl-route, so "
        "an edit applies without a home-manager switch"
    )


def test_the_skill_description_disambiguates_from_a_claude_subagent():
    """🔴 MEASURED: 'dispatch a subagent to implement X' means the Agent tool;
    'dispatch opencode …' means this. The only disambiguating token is literally
    `opencode`, so bare "dispatch" must not route here."""
    fm = SKILL_PATH.read_text().split("---")[1]
    desc = re.search(r"^description:\s*(.+)$", fm, re.M).group(1)
    assert desc.lower().count("opencode") >= 2
    assert "subagent" in desc.lower()
    # The listing budget is 1% of the window with a 1,536-char per-entry cap;
    # on overflow Claude Code silently DROPS descriptions.
    assert len(desc) <= 1536, len(desc)
