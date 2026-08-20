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


def test_scan_paths_accepts_the_in_dir_control(tmp_path):
    d = tmp_path / "proj"
    (d / "sub").mkdir(parents=True)
    inside = d / "sub" / "thing.py"
    assert brief_scan.scan_paths(f"Edit {inside} and re-run.", d) == []


def test_scan_paths_ignores_the_enumerated_system_prefixes(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    assert brief_scan.scan_paths("pipe it to /dev/null with /usr/bin/env", d) == []


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


def test_the_base_ruleset_is_the_config_file_itself():
    rules = oc_permissions.base_bash_rules()
    assert rules[0] == ("*", "allow")
    assert ("*kubectl*exec*", "ask") in rules


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
@pytest.mark.parametrize("rel", [
    "scripts/opencode/SKILL.md",
    "scripts/opencode/opencode-dispatch",
    "scripts/opencode/lib/oc_permissions.py",
    "scripts/opencode/lib/brief_scan.py",
    "scripts/opencode/tests/test_dispatch.py",
])
def test_every_new_file_is_git_tracked(rel):
    """🔴 A new file that is not `git add`ed is silently omitted from the flake.
    The switch SUCCEEDS and the file is simply not there."""
    p = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch", rel],
                       capture_output=True, text=True)
    assert p.returncode == 0, f"{rel} is not tracked — `git add` it"


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
