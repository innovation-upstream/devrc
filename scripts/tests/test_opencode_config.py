"""Tests for the home-manager-managed opencode configuration.

WHAT IS UNDER TEST (see scripts/opencode/README.md for the measurements):

  1. The GENERATED ~/.config/opencode/AGENTS.md. `nix/home.nix` builds it by
     CONCATENATING claude/PRINCIPLES.md + claude/RULES.md +
     claude/opencode-addendum.md at switch time. It has to be a concatenation
     because opencode does NOT expand `@`-imports in AGENTS.md/CLAUDE.md
     (measured on v1.18.4 with an all-tools-denied agent, so no file read was
     possible: an imported passphrase returned NONE, the same content inline
     returned verbatim). ~/.claude/CLAUDE.md is ~1.5 KB of `@PRINCIPLES.md` +
     `@RULES.md` import lines, so if opencode read THAT it would receive none of
     the 32 KB of actual rules. These tests are the regression guard for that
     whole point: they assert the rules TEXT is present, not merely that a file
     exists.

  2. 🔴 The `bash` permission ordering in opencode.jsonc. opencode is
     LAST-MATCH-WINS (the INVERSE of Claude Code), so `"*": "allow"` must be the
     FIRST key and every deny/ask must follow it. If someone sorts these keys,
     every deny silently stops applying and nothing else fails. That is the
     single most load-bearing assertion in this file.

  3. The `shell.env` plugin's DEPLOYED SHAPE. opencode's plugin glob is
     `{plugin,plugins}/*.{ts,js}` — non-recursive, `.ts`/`.js` only. A `.mjs`,
     or a plugin one directory deeper, silently does not load.

  4. The subagent definitions parse as YAML frontmatter + body.

Hermetic: pure file reads under the repo. No opencode, no network, no switch.

    run:  python -m pytest scripts/tests/test_opencode_config.py -q
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLAUDE_DIR = ROOT / "claude"
OC_DIR = ROOT / "scripts" / "opencode"
HOME_NIX = ROOT / "nix" / "home.nix"

PRINCIPLES = CLAUDE_DIR / "PRINCIPLES.md"
RULES = CLAUDE_DIR / "RULES.md"
ADDENDUM = CLAUDE_DIR / "opencode-addendum.md"

AGENT_DIR = OC_DIR / "agent"
EXPECTED_AGENTS = {"nav", "k8s", "review"}

# Ceiling for the generated instruction file. The real thing is ~35 KB; a
# 331 KB AGENTS.md puts opencode into a PERMANENT COMPACTION LOOP. 100 KB is a
# deliberately loose bound — it only has to catch growth into that failure mode.
AGENTS_MD_MAX_BYTES = 100 * 1024


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def generated_agents_md() -> str:
    """Reproduce EXACTLY what nix/home.nix concatenates at switch time.

    Kept in lockstep with home.nix by test_home_nix_generates_agents_md_by
    _concatenation below, which asserts home.nix really does read these three
    files in this order. Without that companion test this helper would be
    asserting against a fiction of its own making.
    """
    return (
        PRINCIPLES.read_text()
        + "\n\n"
        + RULES.read_text()
        + "\n\n"
        + ADDENDUM.read_text()
    )


def strip_jsonc(text: str) -> str:
    """Strip `//` and `/* */` comments, leaving string literals untouched.

    Hand-rolled rather than regex because opencode.jsonc contains `https://`
    inside a string — a naive `//`-to-end-of-line regex would eat the rest of
    the `$schema` line and the file would not parse. Validated by
    test_strip_jsonc_preserves_urls_in_strings below.
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def load_config() -> dict:
    """Parse scripts/opencode/opencode.jsonc, PRESERVING key order.

    json.loads yields a dict, and dicts preserve insertion order — which is what
    makes the last-match-wins ordering assertion possible at all.
    """
    return json.loads(strip_jsonc((OC_DIR / "opencode.jsonc").read_text()))


def parse_frontmatter(text: str):
    """Return (frontmatter_text, body) for a `---`-delimited markdown file.

    🔴 Anchored on the FIRST TWO `---` lines only. A naive `text.split('---')`
    re-matches horizontal rules and `---` inside the body and reports a false
    parse failure — two extractors were bitten by exactly that.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise AssertionError("file does not start with a '---' frontmatter fence")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1:])
    raise AssertionError("frontmatter fence is never closed")


def frontmatter_top_level_keys(fm: str) -> set:
    """Top-level (column-0) `key:` names in a YAML frontmatter block."""
    return {
        m.group(1)
        for m in (re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):", ln) for ln in fm.split("\n"))
        if m
    }


# --------------------------------------------------------------------------- #
# harness self-validation  (negative controls)
#
# 🔴 A harness that reports green while testing nothing is worse than no test.
# These feed each helper a case it MUST reject, so the greens below mean
# something. They are INVARIANT GUARDS on the harness, not regression coverage
# for any shipped bug.
# --------------------------------------------------------------------------- #
def test_strip_jsonc_preserves_urls_in_strings():
    """`https://` must survive; a real comment must not."""
    src = '{"a": "https://x.example/y", // gone\n "b": 1}'
    assert json.loads(strip_jsonc(src)) == {"a": "https://x.example/y", "b": 1}


def test_strip_jsonc_removes_block_comments():
    assert json.loads(strip_jsonc('{/* c */"a": 1}')) == {"a": 1}


def test_first_bash_key_check_rejects_a_reordered_block():
    """MUTATION CONTROL for the ordering assertion.

    Feed the checker a bash block whose keys were sorted so `"*"` is no longer
    first — the exact "tidy-up" that silently disables every deny. If this
    passes, test_bash_permission_wildcard_is_first below is vacuous.
    """
    mutated = {"git stash*": "deny", "*": "allow"}
    assert next(iter(mutated)) != "*", "mutation control did not actually reorder"


def test_parse_frontmatter_rejects_a_file_without_frontmatter():
    with pytest.raises(AssertionError):
        parse_frontmatter("no frontmatter here\n---\nbody")


def test_parse_frontmatter_is_not_confused_by_body_dashes():
    fm, body = parse_frontmatter("---\nk: v\n---\nintro\n\n---\n\nmore\n")
    assert fm.strip() == "k: v"
    assert "more" in body


# --------------------------------------------------------------------------- #
# 1. the generated AGENTS.md
# --------------------------------------------------------------------------- #
def test_source_files_exist():
    for p in (PRINCIPLES, RULES, ADDENDUM):
        assert p.is_file(), f"missing AGENTS.md source: {p}"


def test_home_nix_generates_agents_md_by_concatenation():
    """home.nix must build the file from all THREE sources, by readFile.

    This is what stops generated_agents_md() from testing a fiction. A
    `home.file.".config/opencode/AGENTS.md".source = ...` symlink, or a build
    that dropped RULES.md, would leave every content assertion below asserting
    about text opencode never sees.
    """
    nix = HOME_NIX.read_text()
    assert 'home.file.".config/opencode/AGENTS.md".text' in nix, (
        "AGENTS.md must be GENERATED (.text), not symlinked (.source) — a "
        "symlink to CLAUDE.md would deliver only unexpanded @-import lines"
    )
    for src in (
        "builtins.readFile ../claude/PRINCIPLES.md",
        "builtins.readFile ../claude/RULES.md",
        "builtins.readFile ../claude/opencode-addendum.md",
    ):
        assert src in nix, f"home.nix does not concatenate {src}"


@pytest.mark.parametrize(
    "needle",
    [
        # --- from PRINCIPLES.md ---
        "Software Engineering Principles",
        "Evidence > assumptions",
        "KISS / YAGNI / DRY",
        # --- from RULES.md --- these are the whole reason the file is generated
        "Verification Honesty",
        "git stash",
        "Never `git add -A`",
        "Never `git reset --hard`",
        "Git Workflow",
        "Deployed ≠ verified",
        # --- from the opencode addendum ---
        "$KC_HOMELAB",
        "$KC_WORKBENCH",
        "$KC_PROD",
        "$HOMELAB",
    ],
)
def test_generated_agents_md_carries_the_rules_text(needle):
    """CONTENT, not existence.

    The failure this guards against is opencode being pointed at a file that
    parses fine and contains none of the rules — which is precisely what
    happens if it reads ~/.claude/CLAUDE.md, since its `@`-imports are never
    expanded.
    """
    assert needle in generated_agents_md(), (
        f"the generated AGENTS.md does not contain {needle!r} — opencode would "
        f"run without that rule"
    )


def test_generated_agents_md_has_no_unexpanded_imports():
    """No `@file.md` import line may survive into the generated file.

    opencode does not expand them, so an import line is not a pointer — it is a
    silently missing chunk of the ruleset.
    """
    offenders = [
        (i + 1, ln)
        for i, ln in enumerate(generated_agents_md().split("\n"))
        if re.match(r"^\s*@[\w./-]+\.md\s*$", ln)
    ]
    assert not offenders, (
        "unexpanded @-import lines in the generated AGENTS.md (opencode will "
        f"NOT expand these, so their content is missing): {offenders}"
    )


def test_generated_agents_md_size_is_sane():
    size = len(generated_agents_md().encode("utf-8"))
    assert size > 8 * 1024, (
        f"generated AGENTS.md is only {size} B — far too small to contain "
        f"RULES.md; the concatenation is probably broken"
    )
    assert size < AGENTS_MD_MAX_BYTES, (
        f"generated AGENTS.md is {size} B (> {AGENTS_MD_MAX_BYTES} B). A very "
        f"large AGENTS.md drives opencode into a permanent compaction loop."
    )


# --------------------------------------------------------------------------- #
# 2. opencode.jsonc
# --------------------------------------------------------------------------- #
def test_opencode_jsonc_parses():
    cfg = load_config()
    assert isinstance(cfg, dict) and cfg, "opencode.jsonc parsed to an empty/non-object"


def test_bash_permission_wildcard_is_first():
    """🔴 THE load-bearing assertion.

    opencode resolves permission globs LAST-MATCH-WINS — the inverse of Claude
    Code. `"*": "allow"` must therefore be the FIRST key of the bash block, with
    every deny/ask after it. Sort these keys alphabetically and every deny stops
    applying, with no error anywhere.

    Its mutation control is test_first_bash_key_check_rejects_a_reordered_block.
    """
    bash = load_config()["permission"]["bash"]
    keys = list(bash)
    assert keys[0] == "*", (
        f"first key of the bash permission block is {keys[0]!r}, not '*'. "
        f"opencode is LAST-MATCH-WINS: with '*' not first, every deny below it "
        f"is dead. Do not sort these keys."
    )
    assert bash["*"] == "allow"


@pytest.mark.parametrize(
    "pattern",
    [
        "git add -A*",
        "git add --all*",
        "git add .",
        "git add ..*",
        "git reset --hard*",
        "git stash*",
        "git clean -fd*",
        "rm -rf /*",
        "rm -rf ~*",
        "talosctl reset*",
    ],
)
def test_destructive_bash_patterns_are_denied(pattern):
    bash = load_config()["permission"]["bash"]
    assert bash.get(pattern) == "deny", f"{pattern!r} must be denied, got {bash.get(pattern)!r}"


@pytest.mark.parametrize(
    "pattern",
    [
        "git push*", "git commit*", "git rebase*", "gh pr merge*",
        "kubectl delete*", "kubectl apply*", "kubectl patch*",
        "kubectl scale*", "kubectl drain*",
        "helm upgrade*", "helm uninstall*",
        "flux suspend*", "flux resume*",
        "talosctl patch*", "talosctl reboot*",
        "sops -d*", "sops --decrypt*",
        "nixos-rebuild*", "home-manager switch*", "nix-collect-garbage*",
    ],
)
def test_high_blast_radius_bash_patterns_ask(pattern):
    bash = load_config()["permission"]["bash"]
    assert bash.get(pattern) == "ask", f"{pattern!r} must ask, got {bash.get(pattern)!r}"


def test_every_deny_and_ask_comes_after_the_wildcard():
    """Structural restatement of the ordering rule.

    test_bash_permission_wildcard_is_first pins the first key; this pins that
    NOTHING non-allow precedes it, so a future edit cannot slip a deny above the
    wildcard (where it would be overridden) while leaving `"*"` looking fine at
    index 1.
    """
    bash = load_config()["permission"]["bash"]
    star = list(bash).index("*")
    before = [k for k in list(bash)[:star] if bash[k] != "allow"]
    assert not before, f"deny/ask rules appear BEFORE the '*' wildcard and are dead: {before}"


def test_cheap_model_pinned_on_the_hidden_system_agents():
    """`small_model` covers TITLE GENERATION ONLY — not compaction.

    The actual cheap-model lever is pinning the hidden agents, so a regression
    here is a silent cost increase with no functional symptom.
    """
    agents = load_config()["agent"]
    for name in ("title", "summary", "compaction"):
        assert name in agents, f"hidden agent {name!r} is not pinned to a model"
        assert "flash" in agents[name]["model"], (
            f"{name!r} is pinned to {agents[name]['model']!r}, not the cheap model"
        )


def test_plan_agent_is_genuinely_read_only():
    plan = load_config()["agent"]["plan"]["permission"]
    assert plan["edit"] == "deny"
    assert plan["bash"] == "deny"


def test_no_deprecated_keys():
    """Keys deprecated in opencode 1.18.4 — setting them is a silent no-op."""
    cfg = load_config()
    for dead in ("mode", "layout", "autoshare", "reference", "maxSteps", "theme", "keybinds", "tui"):
        assert dead not in cfg, f"{dead!r} is deprecated on 1.18.4 and must not be set"


def test_core_settings():
    cfg = load_config()
    assert cfg["autoupdate"] is False
    assert cfg["share"] == "disabled"
    assert cfg["snapshot"] is True
    assert cfg["subagent_depth"] == 2
    assert cfg["compaction"] == {"auto": True, "prune": True, "reserved": 16000}
    assert cfg["tool_output"] == {"max_lines": 800, "max_bytes": 24576}
    assert set(cfg["watcher"]["ignore"]) == {
        "node_modules/**", ".git/**", "result/**", ".direnv/**", "**/*.enc.yaml",
    }


# --------------------------------------------------------------------------- #
# 3. the shell.env plugin
# --------------------------------------------------------------------------- #
def test_env_plugin_is_a_js_file():
    """🔴 opencode's plugin glob is `{plugin,plugins}/*.{ts,js}`.

    A `.mjs` does NOT load. Extension is load-bearing.
    """
    js = OC_DIR / "env.js"
    assert js.is_file(), "scripts/opencode/env.js is missing"
    assert js.suffix == ".js"
    assert not (OC_DIR / "env.mjs").exists(), "a .mjs plugin will NOT be loaded by opencode"


def test_env_plugin_deploys_directly_into_plugin_dir():
    """The glob is NON-RECURSIVE — `plugin/sub/env.js` would never load."""
    nix = HOME_NIX.read_text()
    assert 'home.file.".config/opencode/plugin/env.js".source = ../scripts/opencode/env.js;' in nix, (
        "env.js must be deployed as a single file directly at "
        "~/.config/opencode/plugin/env.js (the plugin glob is non-recursive)"
    )
    # A recursive dir symlink at plugin/ would be the tempting refactor that
    # breaks it the moment anything is nested.
    assert 'home.file.".config/opencode/plugin" =' not in nix


def test_env_plugin_registers_a_shell_env_hook():
    src = (OC_DIR / "env.js").read_text()
    assert '"shell.env"' in src, (
        "the plugin must register a `shell.env` hook — there is NO `env` config "
        "key in opencode, so this hook is the only way to reach the bash tool"
    )
    assert "output.env" in src, "the hook must MUTATE output.env"
    assert re.search(r"\bexport\b", src), "the plugin must be exported"


@pytest.mark.parametrize(
    "var,expected",
    [
        ("HOMELAB", "/home/zach/workspace/homelab-talos"),
        ("KC_HOMELAB", "/home/zach/workspace/homelab-talos/homelab-kubeconfig"),
        ("KC_WORKBENCH", "/home/zach/workspace/homelab-talos/workbench-kubeconfig"),
        ("KC_PROD", "/home/zach/workspace/homelab-talos/production-kubeconfig"),
    ],
)
def test_env_plugin_defines_all_four_handles(var, expected):
    """Pinned to LITERAL expected values.

    Deriving the expectation from the implementation (e.g. re-computing it from
    a HOMELAB constant read out of the same file) would keep passing if the
    constant itself were wrong.
    """
    src = (OC_DIR / "env.js").read_text()
    assert f"output.env.{var}" in src, f"plugin does not set {var}"
    tail = expected.replace("/home/zach/workspace/homelab-talos", "")
    if tail:
        assert tail.lstrip("/") in src, f"{var} does not resolve to {expected}"


# --------------------------------------------------------------------------- #
# 4. the subagents
# --------------------------------------------------------------------------- #
def test_exactly_the_expected_agents_exist():
    """Every EXTRA subagent permanently enlarges the primary agent's `task`
    tool description on every request — so the set is capped deliberately."""
    found = {p.stem for p in AGENT_DIR.glob("*.md")}
    assert found == EXPECTED_AGENTS, f"expected {EXPECTED_AGENTS}, found {found}"


@pytest.mark.parametrize("name", sorted(EXPECTED_AGENTS))
def test_agent_frontmatter_is_valid(name):
    text = (AGENT_DIR / f"{name}.md").read_text()
    fm, body = parse_frontmatter(text)
    keys = frontmatter_top_level_keys(fm)

    assert "description" in keys, f"{name}.md frontmatter has no `description`"
    assert "mode" in keys, f"{name}.md frontmatter has no `mode`"

    desc = re.search(r"^description:\s*(.+)$", fm, re.M).group(1).strip()
    assert len(desc) > 20, f"{name}.md description is too short to route on: {desc!r}"

    mode = re.search(r"^mode:\s*(\S+)", fm, re.M).group(1)
    assert mode == "subagent", f"{name}.md mode is {mode!r}, expected 'subagent'"

    assert body.strip(), f"{name}.md has an empty body (no system prompt)"

    if "yaml" in globals():  # pragma: no cover
        pass


def test_agent_frontmatter_parses_as_yaml():
    """Belt-and-braces: the frontmatter must be real YAML, not just look like it."""
    yaml = pytest.importorskip("yaml")
    for name in sorted(EXPECTED_AGENTS):
        fm, _ = parse_frontmatter((AGENT_DIR / f"{name}.md").read_text())
        data = yaml.safe_load(fm)
        assert isinstance(data, dict), f"{name}.md frontmatter is not a YAML mapping"
        assert data.get("mode") == "subagent"
        assert isinstance(data.get("description"), str) and data["description"]


def test_nav_has_no_shell():
    """🔴 `bash: deny` on nav is the DETERMINISTIC fix for ~356 file-navigation
    shell-outs. A prose instruction to "prefer the native tools" is exactly the
    heuristic patch this replaces — if this flips to allow, the fix is gone."""
    fm, body = parse_frontmatter((AGENT_DIR / "nav.md").read_text())
    yaml = pytest.importorskip("yaml")
    perm = yaml.safe_load(fm)["permission"]
    assert perm["bash"] == "deny", "nav MUST have bash denied"
    assert perm["edit"] == "deny"
    assert perm["write"] == "deny"


def test_nav_is_kept_lean():
    """nav must not carry the skill catalogue or be able to recurse.

    VERIFIED against `opencode debug agent nav` on 1.18.4: with these denies the
    resolved tool set is exactly {glob, grep, read} (+ the internal `invalid`).
    Without `skill: deny` it also carries {skill, task, todowrite, webfetch} —
    and `skill` alone injects the whole catalogue, measured at ~3,730 tokens on
    EVERY request. This is a cost guard, and it is the high-frequency agent.
    """
    yaml = pytest.importorskip("yaml")
    fm, _ = parse_frontmatter((AGENT_DIR / "nav.md").read_text())
    perm = yaml.safe_load(fm)["permission"]
    for tool in ("skill", "task"):
        assert perm.get(tool) == "deny", (
            f"nav must deny {tool!r} — it is the cheap high-frequency navigator "
            f"and `skill` alone costs ~3,730 tokens per request"
        )


def test_no_agent_promises_a_list_tool():
    """There is NO `list` tool on opencode 1.18.4.

    Measured: the resolved tool map is exactly {bash, edit, glob, grep, invalid,
    question, read, skill, task, todowrite, webfetch, write}. A prompt that
    tells an agent to "use `list`" sends it hunting for a tool that does not
    exist — and the likely fallback is exactly the shell-out these agents were
    built to stop.
    """
    targets = [AGENT_DIR / f"{n}.md" for n in EXPECTED_AGENTS] + [ADDENDUM]
    bad = []
    for p in targets:
        for i, ln in enumerate(p.read_text().split("\n"), 1):
            # a backticked `list` presented as a tool name
            if re.search(r"`list`", ln) and "no `list` tool" not in ln:
                bad.append(f"{p.name}:{i}: {ln.strip()}")
    assert not bad, f"reference(s) to a nonexistent `list` tool: {bad}"


def test_review_denies_bash_first_then_allows_read_only_git():
    """review's block is the INVERSE shape of the global one: a `"*": "deny"`
    FIRST, then the read-only git/rg allows after it. Last match wins, so the
    allows must come second.

    MEASURED on 1.18.4: agent rules are APPENDED AFTER the global block, giving
    resolved order [0] allow * … [31] deny * [32..36] these allows. So [31] is
    the effective default and any non-allowlisted command resolves deny — the
    restriction is real. NOTE it does NOT prune bash from the request schema
    (bash stays `true` in the resolved tool map, because the global `"*":
    "allow"` at [0] keeps it present): this buys SAFETY, not tokens.
    """
    yaml = pytest.importorskip("yaml")
    fm, _ = parse_frontmatter((AGENT_DIR / "review.md").read_text())
    bash = yaml.safe_load(fm)["permission"]["bash"]
    keys = list(bash)
    assert keys[0] == "*" and bash["*"] == "deny", (
        f"review's bash block must start with '*': deny, got {keys[0]!r}"
    )
    for allowed in ("git diff*", "git log*", "git show*", "git status*"):
        assert bash.get(allowed) == "allow"
    assert all(bash[k] == "allow" for k in keys[1:]), "only allows may follow the deny-all"


def test_k8s_names_the_handles_verbatim():
    """The agent prompt must carry the exported handles, not a constructed path
    — a constructed kubeconfig is how a command silently hits the WRONG
    cluster."""
    text = (AGENT_DIR / "k8s.md").read_text()
    for handle in ("$KC_HOMELAB", "$KC_WORKBENCH", "$KC_PROD", "$HOMELAB"):
        assert handle in text, f"k8s.md does not mention {handle}"
    for phrase in ("trunk", "rollout restart", "Never construct"):
        assert phrase in text, f"k8s.md is missing guidance on {phrase!r}"


def test_k8s_asks_before_mutating():
    yaml = pytest.importorskip("yaml")
    fm, _ = parse_frontmatter((AGENT_DIR / "k8s.md").read_text())
    perm = yaml.safe_load(fm)["permission"]
    assert perm["edit"] == "deny" and perm["write"] == "deny"
    bash = perm["bash"]
    assert list(bash)[0] == "*" and bash["*"] == "allow"
    for pattern in ("kubectl delete*", "kubectl apply*", "flux suspend*", "talosctl*"):
        assert bash.get(pattern) == "ask", f"k8s must ask on {pattern!r}"


# --------------------------------------------------------------------------- #
# 5. the pre-existing unmanaged opencode.jsonc
# --------------------------------------------------------------------------- #
def test_switch_cannot_be_blocked_by_the_unmanaged_config():
    """~/.config/opencode/opencode.jsonc exists as a hand-placed REGULAR FILE.

    checkLinkTargets aborts the whole switch on "would be clobbered", and
    `force = true` is NOT sufficient to displace a real file at a managed path
    (measured 2026-07-30 on bash-guard.py). Without an activation step that runs
    BEFORE checkLinkTargets, the first switch just fails.
    """
    nix = HOME_NIX.read_text()
    assert "home.activation.opencodeDropStaleConfig" in nix
    block = nix.split("home.activation.opencodeDropStaleConfig", 1)[1][:800]
    assert 'entryBefore ["checkLinkTargets"]' in block, (
        "the activation step must run BEFORE checkLinkTargets, or the switch "
        "aborts before it ever executes"
    )
    assert "! -L" in block, (
        "must be guarded on ! -L so a legitimately-managed store symlink is "
        "never touched (and so it is a no-op on every later switch)"
    )
    assert "mv" in block and "rm -f" not in block, (
        "the unmanaged file's content exists nowhere else — back it up, do not rm"
    )


# --------------------------------------------------------------------------- #
# 6. browser-agent must NOT inherit the global AGENTS.md
#
# MEASURED on 1.18.4: the global ~/.config/opencode/AGENTS.md IS injected into a
# run whose --dir is an unrelated scratch project (a tool-less probe quoted a
# passphrase planted at the END of the file). Cost: 8,343 input tokens vs 631
# isolated = ~7.7k EXTRA PER REQUEST, with cache.read/cache.write both 0, so it
# is multiplied by $STEPS (default 12) — up to ~90k tokens per run, for an agent
# whose every host tool is denied and which can therefore act on none of it.
# --------------------------------------------------------------------------- #
BROWSER_AGENT = ROOT / "scripts" / "browser-bridge" / "browser-agent"


def test_browser_agent_isolates_its_opencode_config_dir():
    src = BROWSER_AGENT.read_text()
    assert "export OPENCODE_CONFIG_DIR=" in src, (
        "browser-agent must export OPENCODE_CONFIG_DIR — without it every run "
        "pays ~7.7k extra input tokens PER REQUEST (x$STEPS) for the global "
        "AGENTS.md, which that agent cannot act on (all host tools denied)"
    )


def test_browser_agent_config_dir_is_stable_not_per_run():
    """🔴 A FRESH config dir per run is NOT an acceptable implementation.

    Measured: a fresh dir makes opencode materialise package.json + 63 MB of
    node_modules (2.6-4.5 s), and with no network it HUNG — killed at 120 s
    having produced 0 bytes, which trips the harness's own tool-set gate and
    dies with "produced NO output". A warm reused dir runs offline fine.
    """
    src = BROWSER_AGENT.read_text()
    assert "OC_CONFIG_DIR=" in src
    line = next(ln for ln in src.split("\n") if ln.startswith("OC_CONFIG_DIR="))
    assert "mktemp" not in line, (
        "the isolated opencode config dir must be STABLE and REUSED, never "
        "mktemp'd per run — a fresh dir triggers a 63 MB npm install and hangs "
        "offline, producing 0 bytes and tripping the tool-set gate"
    )
    assert ".cache" in line, "expected a stable dir under the cache dir"


def test_browser_agent_fails_loud_if_an_instruction_file_appears():
    """The isolation is silent when it breaks — so it needs its own alarm."""
    src = BROWSER_AGENT.read_text()
    assert '"$OC_CONFIG_DIR/AGENTS.md"' in src, (
        "must check for a stray AGENTS.md in the isolated config dir — its "
        "reappearance would silently restore the full per-request cost"
    )


def test_readme_documents_the_one_time_command():
    readme = (OC_DIR / "README.md").read_text()
    assert "opencode.jsonc" in readme
    assert "mv ~/.config/opencode/opencode.jsonc" in readme, (
        "README must state the exact manual command as a fallback"
    )
