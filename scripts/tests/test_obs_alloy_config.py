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
    for name in ("OBS_PROM_URL", "OBS_LOKI_URL", "OBS_USERNAME", "OBS_PASSWORD", "OBS_HOST"):
        assert f'sys.env("{name}")' in text, f"{name} must be read from the environment"


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

    return [
        (m.group(1).encode().decode("unicode_escape"), m.group(2))
        for m in re.finditer(
            r'stage\.replace\s*\{\s*expression\s*=\s*"((?:[^"\\]|\\.)*)"\s*'
            r'replace\s*=\s*"((?:[^"\\]|\\.)*)"',
            text,
        )
    ]


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

    `>= 3` was not enough: the parser requires `expression` to be immediately
    followed by `replace`, so a rule written with the attributes in the other
    order is skipped silently. Measured — appending a valid 4th stage with
    `replace` first left the parser returning 3 and the whole suite green, with
    the new rule entirely unasserted. Counting the literal blocks makes an
    unparsed rule fail here instead of passing invisibly.
    """
    text = CONFIG.read_text()
    # Count LIVE blocks only: this config discusses `stage.replace` in its
    # comments, and counting those too made the pin fail 3-vs-5 on its first
    # run — the same read-the-prose-not-the-code hazard the nix guard hit.
    live = "\n".join(
        ln for ln in text.splitlines() if not ln.strip().startswith("//")
    )
    declared = live.count("stage.replace {")
    stages = _replace_stages(text)
    assert declared >= 3, f"expected the redaction rules, found {declared} live blocks"
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
    ("req: Authorization: Bearer HEADERJWTaaa.bbb.ccc done",
     "req: Authorization: Bearer [REDACTED] done"),
    ("req: authorization header bearer BAREJWTxxx.yyy.zzz done",
     "req: authorization header bearer [REDACTED] done"),
    # `Basic` as well as `Bearer`: handling only bearer leaked the base64.
    ("req: authorization: Basic dXNlcjpwdw== done",
     "req: authorization: Basic [REDACTED] done"),
    # A quoted JSON key put a `"` between keyword and `:`, so this once did not
    # match AT ALL and shipped byte-identical.
    ('app: {"password": "hunter2xyz", "user": "bob"}',
     'app: {"password": "[REDACTED]", "user": "bob"}'),
    ("mc: The Access Key Id you provided: access_key=KEYVALUE99",
     "mc: The Access Key Id you provided: access_key=[REDACTED]"),
    ("mc: api_key: APIKEYVALUE123 rejected",
     "mc: api_key: [REDACTED] rejected"),
    ("mc: error key AKIAIOSFODNN7EXAMPLE not found",
     "mc: error key [REDACTED-KEYID] not found"),
    # The value class stops at `&`, so the rest of the query string survives.
    # With `\\S+` this became `?token=[REDACTED] HTTP/1.1`, eating user and page.
    ("nginx: GET /v1/items?token=abc123&user=bob&page=2 HTTP/1.1 200 1234",
     "nginx: GET /v1/items?token=[REDACTED]&user=bob&page=2 HTTP/1.1 200 1234"),
    # NEGATIVE CONTROLS — these must pass through untouched.
    ("app: ordinary line about a password policy with no value",
     "app: ordinary line about a password policy with no value"),
    ("systemd: Started foo.service key=value other=thing",
     "systemd: Started foo.service key=value other=thing"),
    ('app: {"password_reset": true, "user": "bob"}',
     'app: {"password_reset": true, "user": "bob"}'),
    # ACCEPTED OVER-REDACTION, asserted so it stays a decision rather than a
    # surprise: a keyword plus separator redacts the next token whatever it is.
    ("app: the token: is missing entirely",
     "app: the token: [REDACTED] missing entirely"),
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
