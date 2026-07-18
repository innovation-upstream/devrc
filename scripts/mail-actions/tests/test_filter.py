"""Stage-1 deterministic-filter tests against real fixtures.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/mail-actions/tests"

The fixtures in fixtures/mail_headers.json are synthetic mail (headers + from +
subject only — NO bodies) modelled on real inbox shapes: the genuine action threads
(Acme Pay/Paygate/BrightCo/Example Host) plus representative noise (github, npm, alert,
newsletters with/without List-Unsubscribe, a no-reply password-expiry).

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


# Genuine action threads — must SURVIVE Stage 1 (reach the LLM). Modelled on real mail.
ACTION_SENDERS = {
    "sales@acmepay.example.com",
    "dana.avery@acmepay.example.com",
    "accounts@paygate.example.com",
    "robin.hayes@brightco.example.com",
    "billing@examplehost.example.com",
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
    # the Example News newsletter carries List-Unsubscribe + Precedence: bulk.
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
    # billing@examplehost.example.com is covered by the billing@* sender pattern even if
    # it ever gained bulk headers.
    res = f.classify(from_addr="billing@examplehost.example.com",
                     subject="Monthly statement",
                     category="personal", headers={"List-Id": "<x>"})
    assert not res.drop and res.reason == "exempt:billing"


def test_resend_dunning_dropped_despite_billing_subject():
    # Cancelled-subscription dunning. Subject "Payment failed" matches the billing
    # regex, but the sender denylist runs FIRST → it drops, not billing-rescued.
    res = f.classify(from_addr="team@notifications.resend.com",
                     subject="Payment failed for owner Resend subscription",
                     category="personal", headers={"Feedback-ID": "x:ses"})
    assert res.drop and res.reason == "sender:denylist"


def test_voip_low_balance_dropped_but_zero_balance_survives():
    # Low-balance warnings are noise (operator: care only when it hits 0). A future
    # $0 / suspended alert has a different subject and MUST survive.
    low = f.classify(from_addr="noreply@voip.ms", subject="VoIP.ms - Low Balance",
                     category="personal", headers={})
    assert low.drop and low.reason == "sender-subject:denylist"
    zero = f.classify(from_addr="noreply@voip.ms",
                      subject="VoIP.ms - Account suspended (balance depleted)",
                      category="personal", headers={})
    assert not zero.drop, "zero-balance/suspended alert must reach the LLM"


def test_hosting_ddos_dropped_but_real_mail_survives():
    # DDoS reports (operator: false alarms) carry Feedback-ID and are no longer
    # billing-exempted → drop. A real support reply (no bulk header) survives; a real
    # invoice (invoice subject) is still billing-rescued.
    ddos = f.classify(from_addr="support@examplehost.example.com",
                      subject="DDoS detected on one of your servers",
                      category="notification", headers={"Feedback-ID": "x:ses"})
    assert ddos.drop and ddos.reason == "header:Feedback-ID"
    reply = f.classify(from_addr="support@examplehost.example.com",
                       subject="Re: ticket #4412 server provisioning",
                       category="personal", headers={})
    assert not reply.drop, "genuine hosting support reply must survive"
    invoice = f.classify(from_addr="support@examplehost.example.com",
                         subject="Your invoice is ready", category="notification",
                         headers={"Feedback-ID": "x:ses"})
    assert not invoice.drop and invoice.reason == "exempt:billing"


def test_avianca_marketing_subdomain_dropped():
    res = f.classify(from_addr="avianca@info.avianca.com",
                     subject="Tu cabina, tu experiencia de vuelo",
                     category="personal", headers={})
    assert res.drop and res.reason == "sender:denylist"


def test_linkedin_billing_noreply_upsell_not_rescued():
    # LinkedIn abuses billing-noreply@ for Sales Navigator upsell; it carries
    # Feedback-ID. Without the broad billing-*@ allowlist it must NOT be exempted →
    # it drops on the bulk header instead of reaching the LLM.
    res = f.classify(from_addr="billing-noreply@linkedin.com",
                     subject="Maximize your Sales Navigator benefits.",
                     category="personal", headers={"Feedback-ID": "x:linkedin"})
    assert res.drop and res.reason == "header:Feedback-ID"


def test_genuine_billing_noreply_invoice_still_rescued():
    # A real invoice from a billing-noreply@ address (transactional subject) is still
    # rescued by the subject regex even without the broad sender pattern.
    res = f.classify(from_addr="billing-noreply@somevendor.com",
                     subject="Your invoice is ready", category="notification",
                     headers={"Feedback-ID": "x:ses"})
    assert not res.drop and res.reason == "exempt:billing"


def test_billing_subject_regex_is_tight():
    # A newsletter that merely MENTIONS billing in prose should NOT trip the exemption
    # (the regex matches whole billing words, not arbitrary substrings).
    res = f.classify(from_addr="newsletter@example-shop.example.com",
                     subject="Tips for invoicing… subscribe!",
                     category="personal",
                     headers={"List-Unsubscribe": "<x>"})
    # 'invoicing' in prose is not in the regex's word set → stays dropped as bulk.
    assert res.drop


def test_feedback_id_marketing_dropped():
    # hubstaff job-matches + LinkedIn lead-digest carry NO List-* headers but DO carry
    # the ESP Feedback-ID header → must drop without burning an LLM call.
    for mid in (45343, 48326):
        res = classify_fixture(BY_ID[mid])
        assert res.drop and res.reason == "header:Feedback-ID", \
            f"feedback-id noise {mid} not dropped ({res.reason})"


def test_feedback_id_unit():
    res = f.classify(from_addr="x@y.com", subject="Weekly digest",
                     category="personal", headers={"Feedback-ID": "abc:ses"})
    assert res.drop and res.reason == "header:Feedback-ID"


def test_billing_invoice_with_feedback_id_still_kept():
    # An invoice that ALSO carries Feedback-ID must still be rescued — the billing
    # exemption runs before the header drop.
    res = f.classify(from_addr="billing@vendor.com", subject="Your invoice is attached",
                     category="notification",
                     headers={"Feedback-ID": "x:ses", "List-Unsubscribe": "<u>"})
    assert not res.drop and res.reason == "exempt:billing"


def test_github_noreply_security_notices_dropped():
    # noreply@github.com account-security audit mail (new key/PAT/OAuth) carries no
    # List-* headers but is automated noise → dropped by the *@github.com denylist.
    res = f.classify(from_addr="noreply@github.com",
                     subject="[GitHub] A new public key was added to vega/vega-api",
                     category="notification", headers={})
    assert res.drop and res.reason == "sender:denylist"


def test_nasdaq_signin_dropped():
    # nasdaq signin/password-expiry notification → denylisted (operator: noise).
    res = classify_fixture(BY_ID[21720])
    assert res.drop and res.reason == "sender:denylist"


def test_aws_google_verification_still_survives():
    # The genuine-verification carve-out: AWS/Google sign-in codes are NOT denylisted
    # and carry no bulk headers → still reach the LLM.
    for addr in ("no-reply@signin.aws.amazon.com", "no-reply@accounts.google.com"):
        res = f.classify(from_addr=addr, subject="Your verification code",
                         category="notification", headers={})
        assert not res.drop, f"{addr} wrongly dropped"


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
