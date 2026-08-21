"""Coverage for scripts/obs/alloy.alloy — the host-telemetry agent config.

An invalid Alloy config takes the whole agent down at startup, and the failure
is silent from the consumer's side: Prometheus simply has no data for that host,
which looks exactly like a host that froze — the signal this pipeline exists to
make legible. So the config is validated in CI rather than at deploy time.

🔴 `alloy fmt` is NOT sufficient and must not be substituted here. It checks
SYNTAX only: it accepted `faster_drop_reason = "..."` (a real typo in this
file's first draft) with rc=0. `alloy validate` resolves component names,
attribute names and inter-component references, and rejects it. The negative
controls below pin that distinction so nobody "simplifies" this to fmt.
"""

from pathlib import Path

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
# The tests below therefore guard the WIRING of that guarantee and the contracts
# that are checkable without the binary. All of them always run.


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


def test_credential_shaped_values_are_redacted():
    text = CONFIG.read_text()
    assert "stage.replace" in text
    for keyword in ("password", "token", "secret", "authorization"):
        assert keyword in text.lower(), f"no redaction rule mentions {keyword}"


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
# run the agent. The tests above therefore skip without alloy — and these two,
# which never skip, pin that the guarantee stays wired.

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
