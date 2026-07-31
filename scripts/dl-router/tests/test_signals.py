"""Identity signals — the deterministic half of matching.

Every case here is drawn from the shape of the first evening of real traffic
(nine downloads, zero auto-files, eight scoring 0.00 "no signal"), reproduced
with SYNTHETIC ids, hosts and names. Nothing about a real library appears in
this file.

  * six of nine came from a Discord CDN, where the page is a SPA and the
    captured context was completely empty -- so the only signal is the channel
    id inside the attachment URL;
  * one came from a file host reached by clicking through a forum thread, with
    no context of its own;
  * one was a forum attachment where context WAS captured, and the tag list was
    the forum's own section names plus other posters' usernames while the
    subject sat in the URL slug and the page title.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import load_url_cases  # noqa: E402
from matcher import (  # noqa: E402
    DISCORD_SITE, KEY_PREFIX_DISCORD, KIND_PERFORMER, SCORE_ALIAS_SITE,
    MatchContext, Matcher, alias_key, discord_alias_key, discord_channel_id,
    host_of, identity_signals, is_structured_key, suspicious_alias_key,
    thread_alias_key,
    thread_slug, title_subject,
)

CHANNEL = "119283746551234567"
CDN = f"https://cdn.discordapp.com/attachments/{CHANNEL}/998877665544332211/clip.mp4"
MEDIA = f"https://media.discordapp.net/attachments/{CHANNEL}/998877665544332211/still.png"


# --- Discord ---------------------------------------------------------------- #
@pytest.mark.parametrize("url", [CDN, MEDIA, CDN + "?ex=abc&is=def&hm=0123"])
def test_discord_channel_id_is_read_from_the_url(url):
    """No DOM scraping: the id is IN the URL, so a Discord UI change cannot
    break it. `media.discordapp.net` serves the same `/attachments/...` shape
    as `cdn.discordapp.com` and needs the identical treatment."""
    assert discord_channel_id(url) == CHANNEL


def test_discord_ephemeral_attachments_are_the_same_shape():
    url = (f"https://cdn.discordapp.com/ephemeral-attachments/{CHANNEL}/"
           "998877665544332211/clip.mp4")
    assert discord_channel_id(url) == CHANNEL


@pytest.mark.parametrize("url", [
    "",
    None,
    "https://example-site.test/attachments/119283746551234567/1/clip.mp4",
    "https://cdn.discordapp.com/avatars/119283746551234567/abc.png",
    "https://cdn.discordapp.com/attachments/not-a-snowflake/1/clip.mp4",
    "https://cdn.discordapp.com/attachments/",
    "https://evil.test/cdn.discordapp.com/attachments/1/2/x.mp4",
])
def test_a_non_discord_attachment_url_yields_nothing(url):
    assert discord_channel_id(url) == ""


def test_a_discord_download_carries_a_site_even_with_no_page_context():
    """Discord is a SPA: `tags: []`, `title: ''`, `pageUrl: ''`.

    Without a site every alias the learner could write would be GLOBAL, which
    is the widest blast radius in the store. Pinning the site from the URL keeps
    everything Discord teaches us site-scoped.
    """
    ctx = MatchContext.from_payload({"url": CDN, "filename": "clip.mp4",
                                     "page": {"tags": [], "title": "",
                                              "url": ""}})
    assert ctx.site == DISCORD_SITE
    sigs = identity_signals(ctx)
    assert [s.key for s in sigs] == [discord_alias_key(CHANNEL)]
    assert sigs[0].site == DISCORD_SITE
    assert sigs[0].kind == "discord-channel"


def test_a_confirmed_channel_matches_later_downloads_at_full_confidence():
    """The whole Discord design: the FIRST download from a channel opens the
    picker, and every later one is deterministic."""
    m = Matcher(["Jane Doe", "other"],
                {(discord_alias_key(CHANNEL), DISCORD_SITE): "Jane Doe"},
                dir_kinds={"Jane Doe": KIND_PERFORMER})
    other_file = CDN.replace("clip.mp4", "totally-different-name.mp4")
    res = m.match(MatchContext.from_payload({"url": other_file,
                                             "filename": "opaque-8f3a1c92.mp4",
                                             "page": {}}))
    assert res.dir == "Jane Doe"
    assert res.confidence == pytest.approx(SCORE_ALIAS_SITE)
    assert res.auto is True
    assert "discord-channel" in res.reason


def test_an_unconfirmed_channel_scores_nothing_and_opens_the_picker():
    m = Matcher(["Jane Doe", "other"], {},
                dir_kinds={"Jane Doe": KIND_PERFORMER})
    res = m.match(MatchContext.from_payload({"url": CDN, "page": {}}))
    assert res.auto is False
    assert res.dir == "other"


def test_a_channel_alias_does_not_leak_to_another_channel():
    other_channel = "220000000000000001"
    m = Matcher(["Jane Doe", "other"],
                {(discord_alias_key(CHANNEL), DISCORD_SITE): "Jane Doe"},
                dir_kinds={"Jane Doe": KIND_PERFORMER})
    url = CDN.replace(CHANNEL, other_channel)
    assert m.match(MatchContext.from_payload({"url": url, "page": {}})).auto \
        is False


# --- forum thread slugs ----------------------------------------------------- #
@pytest.mark.parametrize("url,expected", [
    # xenforo: slug.<id>
    ("https://forum.test/threads/aster-vale-collection.481920/",
     "aster vale collection"),
    # ...with a page suffix. `page-2` is a chrome verb plus a number.
    ("https://forum.test/threads/aster-vale-collection.481920/page-3",
     "aster vale collection"),
    # vbulletin: <id>-slug
    ("https://forum.test/showthread.php/481920-aster-vale-collection",
     "aster vale collection"),
    # a section ABOVE the thread -- the deepest qualifying segment wins
    ("https://forum.test/forums/general-discussion/threads/aster-vale.99/",
     "aster vale"),
])
def test_thread_slug_extraction(url, expected):
    assert thread_slug(url) == expected


@pytest.mark.parametrize("url", [
    # `t`/`topic`/`topics` are NOT anchors: a paginated index route is
    # structurally identical to a Discourse thread, so no adjacency rule can
    # separate them. An id-less Discourse/IPB thread degrades to the picker,
    # which this design calls acceptable — a wrong slug is not.
    "https://board.test/t/aster-vale/1234",
    "https://forum.test/topic/12345-aster-vale/",
    "https://forum.test/topics/general-discussion/2",
    "https://forum.test/t/photography/3",
    "https://forum.test/t/best-of-2024",
    "",
    None,
    "https://forum.test/",
    "https://forum.test/threads/",
    "https://forum.test/threads/481920/",       # id only, no slug
    "https://forum.test/forums/",
    "https://host.test/a/b/c",                  # single short tokens
])
def test_no_slug_where_there_is_none(url):
    assert thread_slug(url) == ""


def test_the_slug_beats_the_tag_list_as_a_subject_phrase():
    """The one forum download that DID capture context scored 0.67 on title
    tokens while its tag list was section names and other posters' usernames.
    The slug is the cleanest carrier of a subject there is -- it cannot be
    polluted by anyone else's content -- so it leads."""
    ctx = MatchContext.from_payload({
        "url": "https://forum.test/attachments/opaque-9f2.mp4",
        "page": {
            "url": "https://forum.test/threads/aster-vale-collection.481920/",
            "site": "forum.test",
            "title": "Aster Vale Collection | Some Forum",
            "tags": ["General Discussion", "poster_1988", "uploader42"],
        },
    })
    phrases = ctx.subject_phrases()
    assert phrases[0] == "aster vale collection"
    assert phrases.index("General Discussion") > 0


def test_a_forum_thread_matches_its_directory_by_slug_alone():
    m = Matcher(["Aster Vale", "other"], {},
                dir_kinds={"Aster Vale": KIND_PERFORMER})
    res = m.match(MatchContext.from_payload({
        "url": "https://forum.test/attachments/opaque-9f2.mp4",
        "page": {"url": "https://forum.test/threads/aster-vale.481920/",
                 "site": "forum.test", "tags": ["General Discussion"]},
    }))
    assert res.dir == "Aster Vale"


# --- page titles ------------------------------------------------------------ #
# Real `<title>` templates. phpBB puts the subject LAST, XenForo puts it FIRST,
# and both are common -- which is why neither position nor size can pick it.
SLUG = "aster vale deluxe photo set"
SUBJECT = "Aster Vale Deluxe Photo Set"


@pytest.mark.parametrize("title", [
    f"{SUBJECT} | Some Forum",                       # XenForo
    f"Some Forum - View topic - {SUBJECT}",          # phpBB
    f"Section | {SUBJECT} | Some Forum",
    f"Page 2 | {SUBJECT} | Some Forum",
    f"Some Forum \u00bb {SUBJECT}",
])
def test_the_title_subject_is_corroborated_against_the_slug(title):
    """`title_subject` asks the anchored URL slug instead of guessing.

    Both guesses were tried and both failed on real templates. "Longest
    surviving segment" returned the SITE name whenever the subject was one
    word; "first surviving segment" then returned `'View topic'` (a phpBB
    constant, so a site-wide 1.00 alias), `'Section'` and `'Page 2'`. There is
    no ordering rule to find, and a third superlative would just relocate the
    failure -- so this corroborates against a signal that is already proven.
    """
    assert title_subject(title, "forum.test", SLUG) == SUBJECT


def test_with_no_slug_there_is_no_title_subject_at_all():
    """FAIL CLOSED. A title subject can only ever be learned when something
    independent has already confirmed what the subject is. Pages with no thread
    slug still MATCH on their raw title through ordinary containment; what they
    no longer do is mint an alias."""
    assert title_subject(f"{SUBJECT} | Some Forum", "forum.test", "") == ""
    assert title_subject("Anything At All", "forum.test") == ""


def test_a_title_that_corroborates_nothing_yields_nothing():
    assert title_subject("Completely Unrelated | Some Forum", "forum.test",
                         SLUG) == ""


@pytest.mark.parametrize("title,site,slug,expected", [
    ("Aster Vale Collection | Some Forum", "someforum.test",
     "aster vale collection", "Aster Vale Collection"),
    ("Some Forum - Aster Vale Collection", "someforum.test",
     "aster vale collection", "Aster Vale Collection"),
    ("", "someforum.test", "aster vale", ""),
])
def test_title_subject_still_drops_the_site_branding(title, site, slug,
                                                     expected):
    assert title_subject(title, site, slug) == expected


# --- cross-host referrer carry ---------------------------------------------- #
def test_a_proven_referrer_carries_the_thread_subject_across_hosts():
    """A file host reached by clicking through a forum thread has no context of
    its own. The forum thread's identity is carried ONLY when the extension
    proved the link (opener tab, or a captured click whose href is this page)
    -- there is deliberately no time window and no "last thread seen"."""
    ctx = MatchContext.from_payload({
        "url": "https://filehost.test/d/AbCdEf",
        "page": {"url": "https://filehost.test/f/AbCdEf", "site": "filehost.test",
                 "tags": [],
                 "referrerUrl": "https://forum.test/threads/aster-vale.481920/",
                 "referrerTitle": "Aster Vale | Some Forum"},
    })
    keys = {(s.key, s.site) for s in identity_signals(ctx)}
    assert (thread_alias_key("aster vale"), "forum.test") in keys
    assert "aster vale" in ctx.subject_phrases()


def test_without_a_proven_referrer_the_file_host_has_nothing():
    ctx = MatchContext.from_payload({
        "url": "https://filehost.test/d/AbCdEf",
        "page": {"url": "https://filehost.test/f/AbCdEf", "site": "filehost.test",
                 "tags": []},
    })
    assert identity_signals(ctx) == []
    assert ctx.subject_phrases() == []


# --- alias keys ------------------------------------------------------------- #
def test_structured_keys_round_trip_through_the_cli_form():
    """`dl-route alias rm 'discord:<id>'` has to remove the row that
    `dl-route alias list` printed."""
    assert alias_key(f"{KEY_PREFIX_DISCORD}{CHANNEL}") == discord_alias_key(CHANNEL)
    assert alias_key("thread:Aster Vale") == thread_alias_key("aster vale")
    assert alias_key("thread:aster-vale") == thread_alias_key("aster vale")
    assert alias_key("Jane Doe") == "janedoe"
    assert alias_key("discord:not-digits") == ""
    assert is_structured_key(discord_alias_key(CHANNEL))
    assert not is_structured_key("janedoe")


# --- the suspicious-key screen ---------------------------------------------- #
def test_a_short_key_is_refused():
    assert suspicious_alias_key("JD", dir_names=["Jane Doe"])


def test_a_phrase_seen_on_many_directories_is_site_chrome():
    """The generalisation of the forum section name that became an alias. It is
    measured from the labelled examples, not read off a word list."""
    why = suspicious_alias_key("General Discussion", dir_names=["Jane Doe"],
                               site="forum.test", spread=3)
    assert why and "different directories" in why


def test_the_site_name_itself_is_never_a_subject():
    why = suspicious_alias_key("Some Forum", dir_names=["Jane Doe"],
                               site="someforum.test")
    assert why and "site name" in why


def test_a_word_shared_by_several_directories_is_a_category_word():
    dirs = ["Live Sets", "Live Recordings", "Live Archive"]
    why = suspicious_alias_key("live", dir_names=dirs)
    assert why


def test_a_handle_shaped_token_is_refused():
    why = suspicious_alias_key("poster1988", dir_names=["Jane Doe"])
    assert why and "handle" in why


def test_a_structured_key_skips_only_the_WORD_rules():
    """Structured keys used to be exempt from the screen entirely.

    The reasoning ("a channel id is an identity, not a word") held; the
    exemption did not. A badly derived identity is still an identity as far as
    the store is concerned, and it lands at 1.00 WITH auto-file rather than at
    0.85 without -- so the worst row the router can write was the one row
    nothing checked. What structured keys skip now is only the two rules that
    describe a word rather than a source.
    """
    channel = discord_alias_key(CHANNEL)
    # ...the word rules do not fire on it: it is short and it is all digits.
    assert suspicious_alias_key(channel, dir_names=["Jane Doe"],
                                site="discord.com") is None
    # ...but the chrome-spread rule still does.
    assert suspicious_alias_key(channel, dir_names=["Jane Doe"], spread=99)


def test_a_section_name_mistaken_for_a_thread_is_still_screened():
    """Defence in depth behind the anchoring fix: even if some future URL shape
    slipped a section name through as a `thread:` key, the spread rule catches
    it once it has been seen on two directories."""
    key = thread_alias_key("general discussion")
    assert suspicious_alias_key("general discussion", key=key,
                                dir_names=["Jane Doe"], site="forum.test",
                                spread=2)


def test_a_real_subject_name_passes_the_screen():
    assert suspicious_alias_key("Aster Nightingale", dir_names=["Jane Doe"],
                                site="forum.test", spread=1) is None


# --- the shared table ------------------------------------------------------- #
# ONE table, TWO implementations (tests/fixtures/url_cases.json). The
# extension's cached fallback runs exactly when the sidecar is unreachable, so
# a divergence between matcher.py and route_core.js is invisible until it
# misfiles something. `identity.test.mjs` asserts the SAME rows.
URL_CASES = load_url_cases()


@pytest.mark.parametrize("case", URL_CASES["discord"],
                         ids=lambda c: c["url"][:60] or "empty")
def test_discord_ids_match_the_shared_table(case):
    assert discord_channel_id(case["url"]) == case["channel"]


@pytest.mark.parametrize("case", URL_CASES["slug"],
                         ids=lambda c: c["url"][:60] or "empty")
def test_thread_slugs_match_the_shared_table(case):
    assert thread_slug(case["url"]) == case["slug"]


@pytest.mark.parametrize("case", URL_CASES["host"],
                         ids=lambda c: c["url"][:60] or "empty")
def test_hosts_match_the_shared_table(case):
    """`urlsplit` and `new URL` disagreed on 9 of 30 hostile inputs — scheme
    handling, IPv6 brackets, IDN punycoding, percent-encoded authorities. The
    host is the SCOPE of an alias, so the rule is written out in both rather
    than delegated to either standard library."""
    assert host_of(case["url"]) == case["host"]


# --- audit blockers: reproduced, then pinned -------------------------------- #
# Each of these FAILED on the first version of this change. The URLs are the
# ones the audit ran; the names are synthetic.
SECTION_URLS = [
    "https://forum.test/forums/general-discussion/threads/aster.99/",
    "https://forum.test/forums/general-discussion.12/",
    "https://forum.test/members/some-poster.4321/",
]


@pytest.mark.parametrize("url", SECTION_URLS)
def test_a_forum_section_or_member_page_is_never_a_thread_identity(url):
    """BLOCKER 1. `thread_slug` took "the deepest segment with >=2 content
    tokens", so when a thread's own slug was ONE word the SECTION name one
    level up won -- and a section name became a 1.00 auto-filing identity key.
    That is the mislearning this whole change exists to remove, relocated from
    the tag list into the URL path and made worse, because an identity outranks
    every other rule.

    The fix is positional, not a better superlative: a slug is the segment
    immediately after a thread route, and nowhere else.
    """
    assert thread_slug(url) != "general discussion"
    assert thread_slug(url) != "some poster"


def test_the_thread_still_wins_when_its_own_slug_is_one_word():
    assert thread_slug(SECTION_URLS[0]) == "aster"


def test_a_section_page_with_no_thread_yields_no_identity_at_all():
    for url in SECTION_URLS[1:]:
        assert thread_slug(url) == ""
        assert identity_signals(
            MatchContext.from_payload({"page": {"url": url}})) == []


def test_a_section_alias_cannot_be_learned_then_hit_from_another_thread():
    """The end-to-end shape the audit reproduced: one correction in a section,
    then a DIFFERENT thread in the same section matching at 1.00."""
    first = SECTION_URLS[0]
    other = "https://forum.test/forums/general-discussion/threads/vale.100/"
    keys = {s.key for s in identity_signals(
        MatchContext.from_payload({"page": {"url": first}}))}
    other_keys = {s.key for s in identity_signals(
        MatchContext.from_payload({"page": {"url": other}}))}
    assert keys and other_keys
    assert keys.isdisjoint(other_keys), \
        "two threads in one section must not share an identity key"


def test_a_downloads_own_filename_is_not_a_pseudo_thread():
    """`/uploads/dsc-0123.jpg` used to mint `thread:dsc-0123`. Camera defaults
    collide across posters, and every download wrote a junk row."""
    for url in ("https://cdn.test/uploads/dsc-0123.jpg",
                "https://cdn.test/i/img-20240101.mp4"):
        assert thread_slug(url) == ""


def test_the_site_name_does_not_win_a_short_title():
    """The site's display name must never become the subject, on a forum where
    the branding test cannot see it (display name unlike the hostname)."""
    assert title_subject("Aster | Some Forum", "forum.test", "aster") == "Aster"


def test_the_phpBB_constant_never_becomes_an_alias():
    """`'View topic'` appears in the title of every phpBB thread on a site. As
    a learned alias it is a site-wide 1.00 auto-file into one directory."""
    for slug in (SLUG, "aster vale", "photography"):
        assert title_subject(f"Some Forum - View topic - {SUBJECT}",
                             "forum.test", slug) != "View topic"


def test_identity_keys_are_near_verbatim_so_threads_do_not_collide():
    """Stopword stripping belongs to fuzzy matching, never to an identity:
    `aster-vale-new-set` and `aster-vale-set` both folded to one key, and
    upsert_alias re-points on conflict, so the collision was silent."""
    a = thread_alias_key(thread_slug(
        "https://forum.test/threads/aster-vale-new-set.222/"))
    b = thread_alias_key(thread_slug(
        "https://forum.test/threads/aster-vale-set.223/"))
    assert a and b and a != b


# --- corroboration needs more than a coincidence ----------------------------- #
@pytest.mark.parametrize("title,slug", [
    ("Section: Photography | Some Forum", "photography meetup"),
    ("Page 12 | Some Forum", "top-12-sets"),
    ("A Long Sentence Mentioning Aster And Vale In Passing | X", "aster vale"),
])
def test_one_shared_token_is_a_coincidence_not_corroboration(title, slug):
    """The whole segment is emitted VERBATIM, so a single incidental overlap
    minted `'Section: Photography'` and `'Page 12'` as subjects."""
    assert title_subject(title, "forum.test", slug) == ""


def test_a_single_word_segment_that_matches_outright_still_corroborates():
    assert title_subject("Aster | Some Forum", "forum.test", "aster") == "Aster"


@pytest.mark.parametrize("phrase", ["w.w.w.", "e.g.", "i.e.", "p.s.", "O. K."])
def test_the_initialism_exemption_has_a_floor_and_a_stopword_check(phrase):
    """It had neither, so it re-opened everything the length floor was for:
    `w.w.w.` folds to `www` (defeating the stopword rule), and the rest fold to
    two-character keys — under the floor this change restored."""
    assert suspicious_alias_key(phrase, dir_names=["Jane Doe"])


def test_a_real_initialised_name_is_still_exempt():
    assert suspicious_alias_key("M.I.A.", dir_names=["Jane Doe"]) is None
