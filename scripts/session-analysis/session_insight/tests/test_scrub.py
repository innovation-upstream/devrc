"""scrub — every SECRET_PATTERN redacted + labelled + counted; private-key block
redacted; internal IPs survive; public IPs redacted only when enabled."""
import scrub


# One synthetic sample per pattern → its expected label slug.
SAMPLES = {
    "aws-key": "AKIA" + "A" * 16,
    "aws-temp-key": "ASIA" + "B" * 16,
    "github-token": "ghp_" + "a" * 36,
    "github-pat": "github_pat_" + "b" * 42,
    "gitlab-token": "glpat-" + "c" * 24,
    "anthropic-key": "sk-ant-" + "d" * 24,
    "openrouter-key": "sk-or-v1-" + "e" * 24,
    "openai-key": "sk-proj-" + "f" * 24,
    "slack-token": "xoxb-" + "1234567890abc",
    "google-key": "AIza" + "g" * 35,
}


def test_each_secret_pattern_redacted_labelled_counted():
    for label, sample in SAMPLES.items():
        text = f"here is a key: {sample} end"
        clean, counts = scrub.scrub(text)
        assert sample not in clean, f"{label} not redacted"
        assert f"<REDACTED:{label}>" in clean, f"{label} label missing"
        assert counts.get(label) == 1, f"{label} count wrong: {counts}"


def test_multiple_matches_counted():
    text = f"{SAMPLES['aws-key']} and {'AKIA' + 'Z' * 16}"
    clean, counts = scrub.scrub(text)
    assert counts["aws-key"] == 2
    assert "AKIA" not in clean


def test_private_key_block_redacted():
    block = ("-----BEGIN RSA PRIVATE KEY-----\n"
             "MIIEpAIBAAKCAQEA0000fakekeymaterial00000\n"
             "-----END RSA PRIVATE KEY-----")
    clean, counts = scrub.scrub(f"secret:\n{block}\ndone")
    assert "PRIVATE KEY" not in clean
    assert "<REDACTED:private-key>" in clean
    assert counts["private-key"] == 1


def test_internal_ips_survive_by_default():
    text = "nebula 10.42.0.100, LAN 192.168.50.94, loopback 127.0.0.1, NodePort 172.16.0.5"
    clean, counts = scrub.scrub(text)
    assert "10.42.0.100" in clean
    assert "192.168.50.94" in clean
    assert "127.0.0.1" in clean
    assert "172.16.0.5" in clean
    assert "public-ip" not in counts


def test_public_ip_redacted_only_when_enabled():
    text = "external 8.8.8.8 vs internal 192.168.1.1"
    clean_off, counts_off = scrub.scrub(text, redact_public_ips=False)
    assert "8.8.8.8" in clean_off and "public-ip" not in counts_off

    clean_on, counts_on = scrub.scrub(text, redact_public_ips=True)
    assert "8.8.8.8" not in clean_on
    assert "<REDACTED:public-ip>" in clean_on
    assert counts_on["public-ip"] == 1
    assert "192.168.1.1" in clean_on   # internal survives even when enabled


def test_version_string_not_treated_as_ip():
    clean, counts = scrub.scrub("version 999.999.999.999", redact_public_ips=True)
    assert "999.999.999.999" in clean
    assert "public-ip" not in counts


def test_empty_text():
    assert scrub.scrub("") == ("", {})
