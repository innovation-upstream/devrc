"""Tests for the dunst DND-bypass rule that lets unit-failure toasts through.

THE DEFECT THIS PINS (measured 2026-08-10 on the workbench): dunst was paused
(`is-paused=true`, pause level 100) with 30 notifications queued behind it, and
`drift-check.service` was sitting in `failed`. The OnFailure handler ran and sent
its toast — and the toast went into the WAITING queue, was never displayed, and
never even reached `dunstctl history` (history only records toasts that were
DISPLAYED then dismissed/expired). The alert existed and no human could see it.

TWO independent suppressors have to be defeated, so there are two value asserts:

  * `override_pause_level = 100` — the bar's DND button runs
    `dunstctl set-paused true`, which sets pause level 100 (the maximum).
    dunst(5) documents the comparison as "greater than", which would make 100
    unbeatable; the implementation actually compares >=. Measured on dunst
    1.13.2 at pause level 100: override 100 -> displayed 0->1; override 99 ->
    displayed 0->0, waiting 0->1. 100 is necessary AND sufficient.

  * `fullscreen = "show"` — the filterless `fullscreen_suppress` rule also
    matches this toast and would route it straight to history whenever a
    fullscreen window is focused.

Plus an ORDERING assert and a SEAM assert (see the individual docstrings). These
are structural, not spelled: they assert the parsed key/value pairs of the named
rule, so an unrelated occurrence of the word "show" or "100" elsewhere in the
file cannot satisfy them.
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOME_NIX = os.path.join(_HERE, "..", "..", "nix", "home.nix")
_HANDLER = os.path.join(_HERE, "..", "notify-failure.sh")
_BYPASS_RULE = "zz_notify_failure_bypass"
_SUPPRESS_RULE = "fullscreen_suppress"


def _read(path):
    with open(path) as fh:
        return fh.read()


def _dunst_rule(name):
    """Return {key: value} for the dunst rule `name` in services.dunst.settings.

    Parses the `<name> = { ... };` attrset body and strips nix comments, so a
    value that only appears in a comment cannot satisfy an assertion.
    """
    src = _read(_HOME_NIX)
    m = re.search(
        r"^\s*" + re.escape(name) + r"\s*=\s*\{(.*?)^\s*\};",
        src, re.DOTALL | re.MULTILINE)
    assert m, "dunst rule %r not found in nix/home.nix" % name
    body = re.sub(r"#.*$", "", m.group(1), flags=re.MULTILINE)
    out = {}
    for key, val in re.findall(r"(\w+)\s*=\s*([^;]+);", body):
        out[key] = val.strip().strip('"')
    return out


def _rules_setting(key):
    """Names of every dunst rule in nix/home.nix whose body sets `key`.

    Derived from the file, so this tracks rules added later rather than a list
    baked into the test.
    """
    src = _read(_HOME_NIX)
    m = re.search(r"services\.dunst\s*=\s*\{(.*?)^  \};", src, re.DOTALL | re.MULTILINE)
    assert m, "services.dunst block not found in nix/home.nix"
    block = re.sub(r"#.*$", "", m.group(1), flags=re.MULTILINE)
    names = set()
    for name, body in re.findall(r"^      (\w+)\s*=\s*\{(.*?)^      \};",
                                 block, re.DOTALL | re.MULTILINE):
        if re.search(r"^\s*" + re.escape(key) + r"\s*=", body, re.MULTILINE):
            names.add(name)
    return names


def test_bypass_rule_beats_max_pause_level():
    """override_pause_level must be exactly 100 — the level `set-paused true` sets.

    Not >=99, not "some positive number": 99 was measured to QUEUE at level 100.
    """
    rule = _dunst_rule(_BYPASS_RULE)
    assert rule.get("override_pause_level") == "100", (
        "override_pause_level must be 100; `dunstctl set-paused true` sets pause "
        "level 100 and dunst compares >=, so anything lower is silently queued. "
        "got: %r" % rule.get("override_pause_level"))


def test_bypass_rule_opts_out_of_fullscreen_suppression():
    """The filterless fullscreen_suppress rule must not swallow this toast."""
    rule = _dunst_rule(_BYPASS_RULE)
    assert rule.get("fullscreen") == "show", (
        "fullscreen must be \"show\"; the filterless %s rule otherwise routes "
        "this toast straight to history whenever a fullscreen window is focused. "
        "got: %r" % (_SUPPRESS_RULE, rule.get("fullscreen")))


def test_bypass_rule_is_applied_after_fullscreen_suppress():
    """Ordering guard: home-manager renders sections alphabetically; dunst applies
    rules in file order with last-write-wins. If this rule ever sorted BEFORE
    `fullscreen_suppress`, that rule's `fullscreen = "suppress"` would overwrite
    our `fullscreen = "show"` and the toast would vanish under fullscreen again —
    with every value assertion above still green. Pins the RELATIONSHIP, not the
    name.
    """
    competing = _rules_setting("fullscreen") - {_BYPASS_RULE}
    assert competing, (
        "expected at least one other rule to set `fullscreen` (%r); if that rule "
        "was removed this guard is no longer measuring anything" % _SUPPRESS_RULE)
    assert _BYPASS_RULE in _rules_setting("fullscreen"), (
        "%r must itself set `fullscreen` for this ordering guard to apply" % _BYPASS_RULE)
    later = sorted(competing)[-1]
    assert _BYPASS_RULE > later, (
        "%r must sort AFTER every other dunst rule that sets `fullscreen` (last "
        "one wins); %r sorts later and would overwrite it. home-manager emits "
        "dunstrc sections in alphabetical order." % (_BYPASS_RULE, later))


def test_appname_seam_between_handler_and_rule():
    """SEAM: the rule matches on appname; the handler SETS that appname.

    Each side is independently correct and independently tested, and renaming
    either one silently disables the bypass with no test failing — the toast
    would still be sent, still be critical, and still never be shown. This is the
    only assertion that fails when the two disagree, so it pins the relationship
    rather than either component.
    """
    rule = _dunst_rule(_BYPASS_RULE)
    appname = rule.get("appname")
    assert appname, "bypass rule must key on an appname"

    handler = _read(_HANDLER)
    sent = re.findall(r"notify-send[^\n]*?-a\s+(\S+)", handler)
    assert sent, "no `notify-send -a <appname>` found in notify-failure.sh"
    assert all(a == appname for a in sent), (
        "dunst bypass rule matches appname=%r but notify-failure.sh sends -a %r; "
        "the DND bypass would silently never match" % (appname, sorted(set(sent))))
