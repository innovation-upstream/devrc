"""Coverage for scripts/obs/alloy.alloy — the host-telemetry agent config.

An invalid Alloy config takes the whole agent down at startup, and the failure
is silent from the consumer's side: Prometheus simply has no data for that host,
which looks exactly like a host that froze — the signal this pipeline exists to
make legible. So the config is validated at BUILD time (see below), which is stronger
than a test: an invalid config cannot be deployed at all.

🔴 `alloy fmt` is NOT sufficient and must not be substituted here. It checks
SYNTAX only: it accepted `faster_drop_reason = "..."` (a real typo in this
file's first draft) with rc=0. `alloy validate` resolves component names,
attribute names and inter-component references, and rejects it. The negative
controls below pin that distinction so nobody "simplifies" this to fmt.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "scripts" / "obs" / "alloy.alloy"

# WHERE `alloy validate` ACTUALLY RUNS, AND WHY NOT HERE
# -----------------------------------------------------
# It runs at BUILD TIME, in nix/observability.nix, so an invalid config fails
# `home-manager switch` and cannot be deployed at all. That is strictly stronger
# than a test: there is no window in which a broken config is committed-and-green
# but undeployable-in-practice.
#
# It is not ALSO run here because `alloy`'s closure is 604 MiB — larger than
# opencode and nodejs combined — and adding it to gateTools would charge every
# gate run and every CI build for one file. Tests that skipif on `which("alloy")`
# were written and then deleted: in the gate they would ALWAYS skip, which is
# zero coverage plus skip-ledger churn (#608 consolidated those ledgers precisely
# so a stray skip reds a gate).
#
# Measured 2026-08-20, so the claim above is not theoretical:
#   * `alloy validate` rejects an unrecognized attribute name, a nonexistent
#     component, and a dangling inter-component reference (rc=1 each).
#   * `alloy fmt` accepts an unrecognized attribute name with rc=0 — it passed
#     the real `faster_drop_reason` typo from this file's first draft.
#   * Reintroducing that typo fails `home-manager build`:
#       error: Cannot build '...-alloy-config-validated.drv'
#       > Error: ...alloy.alloy:145:5: unrecognized attribute name "faster_drop_reason"
#
# The tests here therefore guard the WIRING of that guarantee, plus the
# contracts checkable without the binary. NONE of them skip — a skipping test
# would be zero coverage in the gate, which is where this has to hold.


# --- public-repo safety --------------------------------------------------- #

def test_no_endpoint_or_credential_is_hardcoded():
    """devrc is PUBLIC. Every site-specific value must come from the
    environment, supplied by a gitignored per-host EnvironmentFile."""
    text = CONFIG.read_text()
    for name in ("OBS_PROM_URL", "OBS_LOKI_URL", "OBS_HOST"):
        assert f'sys.env("{name}")' in text, f"{name} must be read from the environment"


def test_no_basic_auth_block_until_an_endpoint_actually_requires_one():
    """The receivers are unauthenticated, and Alloy has no conditionals — so a
    `basic_auth` block sourcing empty env values does NOT mean "no auth".

    MEASURED against alloy 1.17.1 with a header-echoing listener:
        basic_auth, username="" password=""  ->  Authorization: Basic Og==
        no basic_auth block                  ->  no Authorization header
    and `Og==` decodes to ":". So the block always asserts credentials. Omitting
    it is the only honest encoding of "these endpoints need no auth".

    Asserts LIVE code only — the config discusses basic_auth at length in its
    comments, including the snippet to paste back if an endpoint ever needs it.
    """
    live = "\n".join(
        ln for ln in CONFIG.read_text().splitlines() if not ln.strip().startswith("//")
    )
    assert "basic_auth" not in live, (
        "a basic_auth block is present: it will send an Authorization header on "
        "every request. If an endpoint genuinely requires auth, update this test "
        "deliberately alongside the config.")


def test_credentials_are_never_embedded_in_a_url():
    """URL userinfo (`http://user:pass@host/...`) does produce a correct auth
    header — measured — but alloy logs `url=` in remote_write error messages, so
    the credentials land in the journal. Never encode them that way."""
    import re

    live = "\n".join(
        ln for ln in CONFIG.read_text().splitlines() if not ln.strip().startswith("//")
    )
    assert not re.search(r'"https?://[^"/]*@', live), "credentials embedded in a URL"


def test_the_only_urls_present_are_env_lookups_or_loopback():
    """A real hostname committed here is internal topology in a public repo.

    Asserts the STATE (no absolute URL literal), not the spelling of any one
    host, so a different real host cannot slip past by not matching a pattern.
    """
    import re

    text = CONFIG.read_text()
    # Strip comments: the prose deliberately mentions paths and rationale.
    body = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("//"))
    urls = re.findall(r'"(https?://[^"]+)"', body)
    offenders = [u for u in urls if not u.startswith(("http://127.0.0.1", "http://localhost"))]
    assert offenders == [], f"hardcoded URL(s) in a public repo: {offenders}"


# --- the transport allowlist: the PRIMARY privacy control ------------------ #
# Five audit rounds each found a defect in the redaction regexes, three of them
# introduced by the preceding fix. So the guarantee is no longer "we can pattern
# -match every secret" — it is "the transport carrying application output is
# never shipped". These pin that, because a `keep` rule silently turning into a
# no-op would restore the whole hazard class with every test still green.


def _relabel_rules(text: str):
    """The rule blocks of loki.relabel "journal", as raw strings."""
    import re

    block = re.search(r'loki\.relabel "journal" \{(.*?)\n\}', text, re.S)
    assert block, "loki.relabel \"journal\" not found"
    live = "\n".join(
        ln for ln in block.group(1).splitlines() if not ln.strip().startswith("//")
    )
    return re.findall(r"rule \{(.*?)\n  \}", live, re.S)


def test_a_transport_allowlist_exists_and_is_a_keep_not_a_drop():
    """`keep` is default-DENY: a transport that does not exist yet is dropped
    rather than shipped. A `drop` rule listing bad transports would be
    default-allow, and would ship anything new by accident."""
    rules = _relabel_rules(CONFIG.read_text())
    keeps = [r for r in rules if 'action' in r and '"keep"' in r]
    assert len(keeps) == 1, f"expected exactly one keep rule, found {len(keeps)}"
    assert "__journal__transport" in keeps[0], "the allowlist must key on transport"


def test_stdout_is_not_in_the_allowlist():
    """`stdout` is 69.9% of this host's journal and carries every content class
    the scrubbing existed for: container output, DB errors echoing values,
    object-store credential errors, agent tooling."""
    import re

    rules = _relabel_rules(CONFIG.read_text())
    keep = next(r for r in rules if '"keep"' in r)
    regex = re.search(r'regex\s*=\s*"([^"]*)"', keep).group(1)
    allowed = regex.split("|")
    assert "stdout" not in allowed, f"stdout would be shipped: {allowed}"
    # And the transports the freeze investigation actually reads must survive.
    for needed in ("kernel", "journal"):
        assert needed in allowed, f"{needed} must be shipped — it is the evidence"


def test_the_allowlist_regex_is_fully_anchored_by_alternation_not_a_substring():
    """A regex like `kern` would match `kernel` but relabel anchors the whole
    value, so assert the entries are exact transport names rather than prefixes."""
    import re

    keep = next(r for r in _relabel_rules(CONFIG.read_text()) if '"keep"' in r)
    regex = re.search(r'regex\s*=\s*"([^"]*)"', keep).group(1)
    known = {"kernel", "journal", "syslog", "stdout", "audit", "driver"}
    for entry in regex.split("|"):
        assert entry in known, f"{entry!r} is not a systemd transport name"


# --- the scrubbing contract ----------------------------------------------- #

def test_notification_bodies_are_dropped():
    """dunst lines carry message CONTENT (observed: '<repo> — turn ran 1m 15s')."""
    text = CONFIG.read_text()
    assert "stage.drop" in text
    assert "^dunst$" in text


# --- redaction, BEHAVIOURALLY ---------------------------------------------- #
# This replaced a guard that merely asserted the words "password"/"token"/
# "secret"/"authorization" appeared somewhere in the file. That guard passed
# while the redaction was broken in THREE independent ways, and it survived a
# mutant that turned the replacement into a total no-op.
#
# The emulator below reproduces alloy 1.17.1's `stage.replace` semantics, each
# clause MEASURED end-to-end (loki.source.file -> loki.process -> loki.echo):
#
#   * The `replace` value is a LITERAL. `${1}` is not expanded — it ships as the
#     characters "${1}".
#   * It substitutes that literal for EVERY CAPTURE GROUP individually, not for
#     the whole match. Two groups => the literal appears twice.
#   * With ZERO capture groups it replaces NOTHING. (This is why a
#     `(?:AKIA|ASIA)...` non-capturing rule silently leaked the entire key.)
#
# Python `re` and Go RE2 agree on these patterns: no backreferences, no
# lookaround, no possessive quantifiers. If a rule ever needs those, this
# emulation stops being valid and the check must move to a real alloy run.


def _replace_stages(text: str):
    """[(expression, replace)] for each stage.replace block, in file order."""
    import re

    # BOTH attribute orders. Requiring expression-then-replace meant a block
    # written the other way was skipped SILENTLY — alloy accepts it and the rule
    # fires, so it was a live, unasserted redaction rule.
    out = []
    for block in re.finditer(r"stage\.replace\s*\{(.*?)\n\s*\}", text, re.S):
        body = block.group(1)
        expr = re.search(r'expression\s*=\s*"((?:[^"\\]|\\.)*)"', body)
        repl = re.search(r'replace\s*=\s*"((?:[^"\\]|\\.)*)"', body)
        if expr and repl:
            out.append((expr.group(1).encode().decode("unicode_escape"), repl.group(1)))
    return out


def _apply(stages, line: str) -> str:
    import re

    for expression, replacement in stages:
        pattern = re.compile(expression)
        out, last = [], 0
        for m in pattern.finditer(line):
            if not m.groups():
                continue  # zero capture groups: alloy replaces nothing
            for gi in range(1, len(m.groups()) + 1):
                if m.start(gi) < 0:
                    continue
                out.append(line[last:m.start(gi)])
                out.append(replacement)
                last = m.end(gi)
        out.append(line[last:])
        line = "".join(out)
    return line


def test_every_replace_stage_in_the_config_is_parsed():
    """POSITIVE CONTROL, pinned TWO-WAY.

    A bare `>= N` floor is not enough: a rule this file's parser cannot read is
    asserted by NOTHING while the suite stays green, and alloy runs it happily.
    Measured three separate ways in: attributes in the reverse order, an opening
    brace written `stage.replace{`, and a same-line comment prefix. Comparing
    the parsed count against the count of live blocks makes an unreadable rule
    fail HERE rather than pass invisibly.
    """
    import re

    text = CONFIG.read_text()
    # Count LIVE blocks only: this config discusses `stage.replace` in its
    # comments, and counting those too made the pin fail 3-vs-5 on its first
    # run — the same read-the-prose-not-the-code hazard the nix guard hit.
    live = "\n".join(
        ln for ln in text.splitlines() if not ln.strip().startswith("//")
    )
    # 🔴 A REGEX, and deliberately UNANCHORED. Two bypasses were measured, each
    # a live rule that alloy accepted and that FIRED, while this pin still
    # passed: `stage.replace{` (no space) defeated a literal `count()`, and a
    # same-line prefix such as `/* x */ stage.replace {` defeated a `^\s*`
    # anchor. `_replace_stages` now also accepts either attribute order, so both
    # halves of that hole are closed rather than one.
    declared = len(re.findall(r"stage\.replace\s*\{", live))
    # 🔴 Parse `live`, not `text`. Parsing the full file let a COMMENTED-OUT
    # block be read in place of a real one: the block regex spanned the dead
    # block into the next rule's closing brace, so the parsed expression came
    # from the comment while the counts still matched and this pin stayed green
    # — with a live rule modelled by nothing. Measured.
    stages = _replace_stages(live)
    assert declared >= 4, f"expected the redaction rules, found {declared} live blocks"
    assert len(stages) == declared, (
        f"parsed {len(stages)} of {declared} live stage.replace blocks — "
        "an unparsed rule is asserted by nothing")
    assert all(expr and repl for expr, repl in stages)


# 🔴 The WHOLE output is pinned, not "the secret is absent".
#
# `secret not in output` is walkable by a PARTIAL redaction, which is the exact
# bug this config shipped with: a rule capturing only the 4-char prefix turned
# AKIAIOSFODNN7EXAMPLE into "[REDACTED-KEYID]IOSFODNN7EXAMPLE", leaking 16 of 20
# characters — and that output does not contain the full key, so an absence
# check passes it.
#
# Every expected value below was MEASURED by feeding these lines through real
# alloy 1.17.1 using the stage.replace blocks extracted from this very config
# (loki.source.file -> loki.process -> loki.echo). The trade is that a cosmetic
# reword of a rule fails these tests; that is the price of a machine-checkable
# claim about redaction.
MEASURED_REDACTIONS = [
    ("app: password: hunter2xyz done",
     "app: password: [REDACTED] done"),
    ("app: PASSWORD=hunter2xyz done",
     "app: PASSWORD=[REDACTED] done"),
    # --- secrets containing delimiter characters -------------------------
    # These were PARTIALLY redacted by a value class that stopped at , ; & " '
    # — the same partial-leak class as the AKIA prefix bug. `&` and `;` are in
    # most password alphabets; Django's get_random_secret_key() includes `&`.
    ("app: password=p@ss;w0rd done",
     "app: password=[REDACTED] done"),
    ("app: password=a&b&c done",
     "app: password=[REDACTED] done"),
    # The trailing quote is consumed: 2b allows `'` in the value class (so that
    # `password=ab'cd` cannot truncate), and therefore eats 2a's closing quote.
    # Cosmetic, and the right side of the trade — measured, not assumed.
    ("app: password='sq;uote' done",
     "app: password='[REDACTED] done"),
    ("env: SECRET_KEY='django-insecure-ab,cd' done",
     "env: SECRET_KEY='[REDACTED] done"),
    ("app: password: \"two words here\" done",
     "app: password: \"[REDACTED]\" done"),
    # --- quote edge cases, each a leak that shipped at some point ---------
    # UNTERMINATED quoted value (journald splits long lines at LineMax). 2a
    # needs a closing quote and 2b could not start on one, so this shipped in
    # CLEAR for one round — a redaction the earlier single-rule version did do.
    ('app: password: "abcdef',
     'app: password: "[REDACTED]'),
    ("app: password: 'abcdef",
     "app: password: '[REDACTED]"),
    # A quote INSIDE an unquoted value truncated to `[REDACTED]'cd`.
    ("app: password=ab'cd done",
     "app: password=[REDACTED] done"),
    # ACCEPTED RESIDUAL: an escaped quote ends 2a's match early, leaving the
    # tail of the value behind. The escape-aware class that fixed this caused a
    # strictly worse leak (see the next case), so it was reverted. JSON is the
    # main producer of this shape and arrives on `stdout`, which the transport
    # allowlist drops entirely.
    ('app: {"password": "a\\"b", "user": "bob"}',
     'app: {"password": "[REDACTED]"b", "user": "bob"}'),
    # 🔴 THE REASON that residual is accepted: with an escape-aware value class,
    # a lone trailing backslash consumed the real closing quote and the match
    # ran to the NEXT quote, shipping the following credential in CLEAR
    # (`token="[REDACTED]"hunter2zz"`). Both are now redacted.
    ('app: token="abc\\" password="hunter2zz"',
     'app: token="[REDACTED]" password="[REDACTED]"'),
    # --- schemes ----------------------------------------------------------
    ("req: Authorization: Bearer JWTaaa.bbb.ccc done",
     "req: Authorization: Bearer [REDACTED] done"),
    ("req: authorization: Basic dXNlcjpwdw== done",
     "req: authorization: Basic [REDACTED] done"),
    # --- structured shapes ------------------------------------------------
    ('app: {"password": "hunter2xyz", "user": "bob"}',
     'app: {"password": "[REDACTED]", "user": "bob"}'),
    ("mc: The Access Key Id you provided: access_key=KEYVALUE99",
     "mc: The Access Key Id you provided: access_key=[REDACTED]"),
    ("mc: api_key: APIKEYVALUE123 rejected",
     "mc: api_key: [REDACTED] rejected"),
    ("mc: error key AKIAIOSFODNN7EXAMPLE not found",
     "mc: error key [REDACTED-KEYID] not found"),
    ("http: token=abc123; Path=/; HttpOnly",
     "http: token=[REDACTED] Path=/; HttpOnly"),
    # --- NEGATIVE CONTROLS: must pass through untouched --------------------
    ("app: ordinary line about a password policy with no value",
     "app: ordinary line about a password policy with no value"),
    ("systemd: Started foo.service key=value other=thing",
     "systemd: Started foo.service key=value other=thing"),
    ('app: {"password_reset": true, "user": "bob"}',
     'app: {"password_reset": true, "user": "bob"}'),
    # --- ACCEPTED OVER-REDACTION, asserted so it stays a decision ----------
    # (a) keyword + separator redacts the next token whatever it is
    ("app: the token: is missing entirely",
     "app: the token: [REDACTED] missing entirely"),
    # (b) a URL query tail is eaten. Preserving it is what required the leaky
    #     value class, so this is the deliberate trade.
    ("nginx: GET /v1/items?token=abc123&user=bob&page=2 HTTP/1.1 200",
     "nginx: GET /v1/items?token=[REDACTED] HTTP/1.1 200"),
    # (c) rule 3 eats the word after "basic" in English prose. 8 lines in
    #     100,609 of this host's journal; 7 are `Reached target Basic System.`
    ("systemd: Reached target Basic System.",
     "systemd: Reached target Basic [REDACTED]"),
    ("kernel: basic block device error",
     "kernel: basic [REDACTED] device error"),
]


@pytest.mark.parametrize("line,expected", MEASURED_REDACTIONS)
def test_redaction_matches_measured_alloy_output(line, expected):
    stages = _replace_stages(CONFIG.read_text())
    assert _apply(stages, line) == expected


def test_redaction_leaves_readable_context_behind():
    """Redacting the whole line would defeat the point of shipping the journal.
    The keyword must survive so the entry is still debuggable."""
    stages = _replace_stages(CONFIG.read_text())
    assert "password" in _apply(stages, "app: password: hunter2xyz done").lower()


def test_redaction_emits_no_literal_template_placeholder():
    """`${1}` is not expanded by stage.replace — a rule written that way ships
    the characters ${1} and destroys the key name."""
    stages = _replace_stages(CONFIG.read_text())
    out = _apply(stages, "app: password: hunter2xyz done")
    assert "${" not in out and "$1" not in out, out


def test_an_ordinary_line_is_not_over_redacted():
    """NEGATIVE CONTROL: a rule broad enough to eat normal log lines would make
    the journal useless, and would pass every assertion above."""
    stages = _replace_stages(CONFIG.read_text())
    line = "app: ordinary line about a password policy with no value"
    assert _apply(stages, line) == line


def test_scrubbing_runs_BEFORE_the_write_not_after():
    """Ordering is the whole point: the journal source must forward into the
    scrub stage, and only the scrub stage may forward to loki.write. A config
    that ships first and scrubs second would validate cleanly and leak."""
    text = CONFIG.read_text()
    assert "forward_to    = [loki.process.scrub.receiver]" in text or \
           "forward_to = [loki.process.scrub.receiver]" in text, \
           "journal source must forward into the scrub stage"

    # The journal source must NOT write straight to Loki.
    source_block = text.split('loki.source.journal "system" {', 1)[1].split("\n}", 1)[0]
    assert "loki.write" not in source_block, \
        "journal source bypasses scrubbing and writes directly to Loki"


def test_metrics_scrape_interval_is_short_enough_to_see_a_freeze():
    """At the 60s default a thermal or IO cliff is one or two points."""
    text = CONFIG.read_text()
    assert 'scrape_interval = "15s"' in text


# --- the deploy-time guarantee -------------------------------------------- #
# `alloy` is not in the gate toolchain: its closure is 604 MiB, more than
# opencode and nodejs combined, which is not worth paying on every CI run to
# check one file. So the AUTHORITATIVE validation happens at build time in
# nix/observability.nix, where the hosts already need the binary because they
# run the agent. NOTHING in this file skips or invokes a binary; the tests below
# pin that the build-time guarantee stays wired.

MODULE = REPO / "nix" / "observability.nix"


def _live_code(path: Path) -> str:
    """Nix source with comment lines removed.

    🔴 Load-bearing. This module DISCUSSES `alloy validate` at length in its
    comments, so a plain `"alloy validate" in text` check passes even after the
    call is deleted — measured: that exact mutant SURVIVED a fully green run.
    A guard that reads the prose describing a mechanism, rather than the
    mechanism, provides no coverage while looking like it does.
    """
    return "\n".join(
        ln for ln in path.read_text().splitlines() if not ln.strip().startswith("#")
    )


def test_the_nix_module_validates_the_config_at_build_time():
    """If this wiring is removed, an invalid config becomes deployable again and
    the failure is silent: the host just stops appearing in Prometheus, which
    looks exactly like the freeze this pipeline exists to detect."""
    body = _live_code(MODULE)
    assert "alloy validate" in body, "build-time validation is gone"
    assert "runCommandLocal" in body
    assert "grafana-alloy" in body


def test_the_build_time_check_is_validate_not_fmt():
    """`alloy fmt` passes an unrecognized attribute name with rc=0 — it accepted
    the `faster_drop_reason` typo. Substituting it here would look equivalent
    and check almost nothing."""
    assert "alloy fmt" not in _live_code(MODULE), "fmt is not sufficient; use `alloy validate`"


def test_credentials_are_read_from_outside_the_nix_store():
    """The nix store is world-readable and this repo is public."""
    text = MODULE.read_text()
    assert "EnvironmentFile" in text
    assert "obs-ship/env" in text
