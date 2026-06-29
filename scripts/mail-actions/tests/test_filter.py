"""Stage-1 deterministic-filter tests against real fixtures.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/mail-actions/tests"

The fixtures in fixtures/mail_headers.json are scrubbed real mail (headers + from +
subject only — NO bodies) pulled from the live inbox: the genuine action threads
(Zen/Stripe/naida/Hetzner) plus representative noise (github, npm, alert, newsletters
with/without List-Unsubscribe, a no-reply password-expiry).

Contract under test: the high-precision tier must NEVER drop a genuine action item,
and MUST drop unambiguous bulk/notification noise.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import filter as f  # noqa: E402

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "mail_headers.json").read_text())
BY_ID = {r["id"]: r for r in FIXTURES}


def classify_fixture(r):
    return f.classify(
        from_addr=r["from_addr"], subject=r["subject"],
        category=r["category"], headers=r["headers"],
    )


# Genuine action threads — must SURVIVE Stage 1 (reach the LLM). Pulled from live mail.
ACTION_SENDERS = {
    "sales@zenpayments.com",
    "kara.redford@zenpayments.com",
    "accounts@stripe.com",
    "lauren@naidacom.com",
    "billing@hetzner.com",
}


def test_genuine_action_threads_survive():
    survived = {r["from_addr"] for r in FIXTURES
                if r["from_addr"] in ACTION_SENDERS and not classify_fixture(r).drop}
    missing = ACTION_SENDERS - survived
    assert not missing, f"genuine action threads wrongly dropped: {missing}"


def test_no_action_thread_is_dropped():
    for r in FIXTURES:
        if r["from_addr"] in ACTION_SENDERS:
            assert not classify_fixture(r).drop, f"dropped action thread {r['id']} {r['from_addr']}"


def test_alert_category_dropped():
    alerts = [r for r in FIXTURES if r["category"] == "alert"]
    assert alerts, "fixture set should contain at least one alert"
    for r in alerts:
        res = classify_fixture(r)
        assert res.drop and res.reason == "category:alert"


def test_github_notifications_dropped():
    gh = [r for r in FIXTURES if r["from_addr"] == "notifications@github.com"]
    assert gh, "fixture set should contain github notifications"
    for r in gh:
        assert classify_fixture(r).drop, f"github notification {r['id']} not dropped"


def test_npm_dropped_via_glob():
    # support@npmjs.com is covered by the `*@npmjs.com` denylist pattern.
    npm = [r for r in FIXTURES if r["from_addr"].endswith("@npmjs.com")]
    assert npm, "fixture set should contain an npmjs.com sender"
    for r in npm:
        res = classify_fixture(r)
        assert res.drop and res.reason == "sender:denylist"


def test_bugsnag_dropped_via_denylist():
    bs = [r for r in FIXTURES if r["from_addr"] == "notifications@bugsnag.com"]
    assert bs
    for r in bs:
        assert classify_fixture(r).drop


def test_newsletter_with_list_unsubscribe_dropped():
    # buildcanada newsletter carries List-Unsubscribe + Precedence: bulk.
    nl = BY_ID[44190]
    res = classify_fixture(nl)
    assert res.drop and res.reason.startswith("header:") or res.reason.startswith("precedence:")


def test_billing_invoice_survives_despite_bulk_headers():
    # Cloudflare invoice (noreply@notify.cloudflare.com) arrives via sparkpost with
    # List-Id + List-Unsubscribe; the billing exemption must rescue it from the bulk
    # drop so the LLM can judge it. Subject "Your invoice is attached".
    cf = BY_ID[14563]
    res = classify_fixture(cf)
    assert not res.drop, "billing invoice wrongly dropped as bulk"
    assert res.reason == "exempt:billing"


def test_billing_sender_allowlist_survives():
    # billing@hetzner.com is covered by the billing@* sender pattern even if it ever
    # gained bulk headers.
    res = f.classify(from_addr="billing@hetzner.com", subject="Monthly statement",
                     category="personal", headers={"List-Id": "<x>"})
    assert not res.drop and res.reason == "exempt:billing"


def test_billing_subject_regex_is_tight():
    # A newsletter that merely MENTIONS billing in prose should NOT trip the exemption
    # (the regex matches whole billing words, not arbitrary substrings).
    res = f.classify(from_addr="newsletter@vetr.com",
                     subject="Tips for invoicing… subscribe!",
                     category="personal",
                     headers={"List-Unsubscribe": "<x>"})
    # 'invoicing' in prose is not in the regex's word set → stays dropped as bulk.
    assert res.drop


def test_noreply_without_list_headers_survives_to_llm():
    # nasdaq password-expiry: no-reply, no List headers → MUST reach the LLM, not be
    # blanket-dropped (could be action-required).
    nasdaq = BY_ID[21720]
    assert not classify_fixture(nasdaq).drop


# -- pure-unit coverage of the primitives ---------------------------------
def test_header_presence_is_case_insensitive():
    assert f.classify(from_addr="x@y.com", subject="s", category="personal",
                      headers={"list-id": "<x>"}).drop


def test_precedence_bulk_dropped():
    res = f.classify(from_addr="x@y.com", subject="s", category="personal",
                     headers={"Precedence": "Bulk"})
    assert res.drop and res.reason == "precedence:bulk"


def test_plain_personal_mail_survives():
    res = f.classify(from_addr="human@example.com", subject="can we meet?",
                     category="personal", headers={"From": "human@example.com"})
    assert not res.drop and res.reason == "survivor"


def test_no_reply_not_blanket_dropped():
    res = f.classify(from_addr="no-reply@signin.aws.amazon.com",
                     subject="Your verification code", category="notification",
                     headers={})
    assert not res.drop
