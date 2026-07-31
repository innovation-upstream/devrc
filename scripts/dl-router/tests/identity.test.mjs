// Identity signals in the extension, and the auto-file gate in the cached
// fallback. Both must agree with the sidecar: the fallback runs exactly when
// the sidecar is unreachable, so any divergence is invisible until it misfiles.
//
// The URL table is shared with tests/test_signals.py -- see fixtures/url_cases.json.
import test from "node:test";
import assert from "node:assert/strict";

import {
  KIND_CATEGORY, KIND_PERFORMER, SCORE_ALIAS_SITE, buildMatchPayload,
  carryReferrer, discordAliasKey, discordChannelId, hostOf, identitySignals,
  kindOf,
  localContext, localDecide, subjectPhrases, threadAliasKey, threadSlug,
  titleSubject,
} from "../extension/route_core.js";
import { loadUrlCases } from "./fixtures.mjs";

const URL_CASES = loadUrlCases();
const CHANNEL = "119283746551234567";
const CDN
  = `https://cdn.discordapp.com/attachments/${CHANNEL}/998877665544332211/clip.mp4`;
const THREAD = "https://someforum.test/threads/aster-vale.481920/";

const DIRS = [
  { name: "Jane Doe", key: "janedoe", tokens: ["jane", "doe"],
    kind: KIND_PERFORMER },
  { name: "Field Notes", key: "fieldnotes", tokens: ["field", "notes"],
    kind: KIND_CATEGORY },
  { name: "Aster Vale", key: "astervale", tokens: ["aster", "vale"] },
  { name: "other", key: "other", tokens: [] },
];

function snapshot(aliases = []) {
  return { dirs: DIRS, aliases, threshold: 0.75, otherDir: "other" };
}

// --- the shared table ------------------------------------------------------- //
test("Discord channel ids match the shared table byte for byte", () => {
  for (const c of URL_CASES.discord) {
    assert.equal(discordChannelId(c.url), c.channel, c.url);
  }
});

test("thread slugs match the shared table byte for byte", () => {
  for (const c of URL_CASES.slug) {
    assert.equal(threadSlug(c.url), c.slug, c.url);
  }
});

test("hostnames match the shared table byte for byte", () => {
  // The host is the SCOPE of an alias. `new URL` and Python's `urlsplit`
  // disagreed on 9 of 30 hostile inputs (scheme handling, IPv6 brackets, IDN
  // punycoding, percent-encoded authorities), so the rule is written out in
  // both rather than delegated to either standard library.
  for (const c of URL_CASES.host) {
    assert.equal(hostOf(c.url), c.host, c.url);
  }
});

// --- Discord ---------------------------------------------------------------- //
test("a Discord attachment yields a site-scoped channel identity", () => {
  // Six of nine real downloads looked like this: `tags: []`, `title: ''`,
  // `pageUrl: ''`. The channel id in the URL is the only signal there is.
  const sigs = identitySignals({ url: CDN });
  assert.deepEqual(sigs, [{ key: discordAliasKey(CHANNEL),
    site: "discord.com", kind: "discord-channel" }]);
});

test("a confirmed channel scores at full confidence from the CACHE", () => {
  const snap = snapshot([
    { key: discordAliasKey(CHANNEL), site: "discord.com", dir: "Jane Doe" },
  ]);
  const out = localDecide({ url: CDN }, snap);
  assert.equal(out.dir, "Jane Doe");
  assert.equal(out.confidence, SCORE_ALIAS_SITE);
  assert.equal(out.auto, true);
  assert.match(out.reason, /discord-channel/);
});

test("an unconfirmed channel scores nothing", () => {
  assert.equal(localDecide({ url: CDN }, snapshot()), null);
});

test("the match payload carries the URLs the identity is derived from", () => {
  // Without them the sidecar cannot see the channel either.
  const payload = buildMatchPayload({ id: 3, url: CDN, filename: "clip.mp4" },
    null);
  assert.equal(payload.url, CDN);
  assert.equal(discordChannelId(localContext(payload).url), CHANNEL);
});

// --- the auto-file gate ----------------------------------------------------- //
test("only a performer directory auto-files from the cache", () => {
  const snap = snapshot([
    { key: "fieldnotes", site: "site.test", dir: "Field Notes" },
  ]);
  const out = localDecide({ tags: ["Field Notes"], site: "site.test" }, snap);
  assert.equal(out.dir, "Field Notes");
  assert.equal(out.confidence, SCORE_ALIAS_SITE);
  assert.equal(out.auto, false, "a category always confirms");
  assert.match(out.reason, /category/);
});

test("an unclassified directory never auto-files from the cache", () => {
  const snap = snapshot([
    { key: "astervale", site: "site.test", dir: "Aster Vale" },
  ]);
  const out = localDecide({ tags: ["Aster Vale"], site: "site.test" }, snap);
  assert.equal(out.auto, false);
  assert.match(out.reason, /unclassified/);
});

test("kindOf reads only the two real kinds", () => {
  assert.equal(kindOf(snapshot(), "Jane Doe"), KIND_PERFORMER);
  assert.equal(kindOf(snapshot(), "Aster Vale"), "unknown");
  assert.equal(kindOf(snapshot(), "nope"), "unknown");
  assert.equal(kindOf({ dirs: [{ name: "x", kind: "performer!" }] }, "x"),
    "unknown");
});

// --- forum threads ---------------------------------------------------------- //
test("the slug leads the subject phrases, ahead of the tag list", () => {
  const phrases = subjectPhrases({
    pageUrl: THREAD,
    site: "someforum.test",
    pageTitle: "Aster Vale | Some Forum",
    tags: ["General Discussion", "poster_1988"],
  });
  assert.equal(phrases[0], "aster vale");
  assert.ok(phrases.indexOf("General Discussion") > 0);
});

test("the title subject is corroborated against the slug, not guessed", () => {
  // phpBB puts the subject LAST, XenForo puts it FIRST, and both are common:
  // neither position nor size can pick it. See matcher.title_subject.
  const SUBJECT = "Aster Vale Deluxe Photo Set";
  const SLUG = "aster vale deluxe photo set";
  for (const title of [
    `${SUBJECT} | Some Forum`,
    `Some Forum - View topic - ${SUBJECT}`,
    `Section | ${SUBJECT} | Some Forum`,
    `Page 2 | ${SUBJECT} | Some Forum`,
  ]) {
    assert.equal(titleSubject(title, "forum.test", SLUG), SUBJECT, title);
  }
});

test("with no slug there is no title subject at all", () => {
  assert.equal(titleSubject("Aster Vale | Some Forum", "someforum.test", ""),
    "");
});

test("the ambiguous routes are not anchors at all", () => {
  // Gating `t`/`topic`/`topics` on an adjacent numeric id does not work: a
  // PAGINATED index is structurally identical to a Discourse thread.
  for (const url of [
    "https://forum.test/topics/general-discussion",
    "https://forum.test/topics/general-discussion/2",
    "https://forum.test/t/photography",
    "https://forum.test/t/photography/3",
    "https://forum.test/t/best-of-2024",
    "https://forum.test/t/aster-vale/1234",
    "https://forum.test/topic/12345-aster-vale/",
  ]) assert.equal(threadSlug(url), "", url);
  // ...while the unambiguous routes still work.
  assert.equal(threadSlug("https://forum.test/threads/aster-vale.99/"),
    "aster vale");
});

test("one shared token is a coincidence, not corroboration", () => {
  assert.equal(titleSubject("Section: Photography | Some Forum", "forum.test",
    "photography meetup"), "");
  assert.equal(titleSubject("Page 12 | X", "forum.test", "top-12-sets"), "");
});

// --- the cross-host referrer carry ------------------------------------------ //
const forumCapture = {
  pageUrl: THREAD, pageTitle: "Aster Vale | Some Forum", tabId: 1,
  href: "https://filehost.test/f/AbCdEf", tags: [],
};
const hostCapture = {
  pageUrl: "https://filehost.test/f/AbCdEf", pageTitle: "Download - Filehost",
  tabId: 2, openerTabId: 1, tags: [],
};

test("a captured click whose href IS this page proves the link", () => {
  const carried = carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    hostCapture, [forumCapture, hostCapture]);
  assert.equal(carried.pageUrl, THREAD);
});

// BLOCKER 3. The opener-tab branch took the newest usable capture whose tabId
// matched `openerTabId`, with nothing binding that capture to the navigation
// that opened the tab and no time bound -- "the last thread I saw in that tab"
// wearing the word "provable". The wrong answer was not just used for one
// match; it was LEARNED, as a 1.00 identity alias.
test("an opener tab alone is NOT a proof — the branch is gone", () => {
  const donor = { ...forumCapture, href: "" };   // never linked here
  assert.equal(carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    hostCapture, [donor, hostCapture]), null);
});

test("the ordinary forum pattern no longer carries the WRONG thread", () => {
  // Open the file host in a new tab, keep browsing the opener tab. By the time
  // the download fires, the newest capture from that tab is a different
  // thread -- and the href proof cannot cover for it, because a `?ref=`
  // parameter on the published href breaks the equality.
  const linked = { ...forumCapture, href: "https://filehost.test/f/AbCdEf?ref=1" };
  const laterThread = {
    pageUrl: "https://someforum.test/threads/someone-else.99/",
    pageTitle: "Someone Else", tabId: 1, href: "", tags: [], ts: Date.now(),
  };
  assert.equal(carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    hostCapture, [linked, laterThread, hostCapture]), null);
});

test("openerTabId can only NARROW the href proof, never invent one", () => {
  // Same proven link, but the donor is not the tab we came from.
  const donor = { ...forumCapture, tabId: 77 };
  assert.equal(carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    hostCapture, [donor, hostCapture]), null);
  // ...and with the tab agreeing, the carry stands.
  const carried = carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    hostCapture, [forumCapture, hostCapture]);
  assert.equal(carried.pageUrl, THREAD);
});

test("a download with no page of its own at all carries nothing", () => {
  assert.equal(carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    null, [forumCapture]), null);
});

test("an unprovable thread is NOT carried, however recent", () => {
  // No time window and no "last thread seen": both get it wrong exactly when
  // several tabs are open, which is how anyone browses a forum.
  const unrelated = { ...forumCapture, href: "", tabId: 9, ts: Date.now() };
  const orphan = { ...hostCapture, openerTabId: undefined };
  assert.equal(carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    orphan, [unrelated, orphan]), null);
});

test("a page with its own tags never imports someone else's subject", () => {
  const rich = { ...hostCapture, tags: ["Jane Doe"] };
  assert.equal(carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    rich, [forumCapture, rich]), null);
});

test("the carried thread becomes an identity signal, scoped to the FORUM", () => {
  const carried = carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    hostCapture, [forumCapture, hostCapture]);
  const payload = buildMatchPayload(
    { id: 1, url: "https://filehost.test/d/AbCdEf" }, hostCapture, carried);
  assert.equal(payload.page.referrerUrl, THREAD);
  const sigs = identitySignals(localContext(payload));
  assert.deepEqual(sigs, [{ key: threadAliasKey("aster vale"),
    site: "someforum.test", kind: "thread-slug" }]);
});

test("a carried thread matches its directory from the cache", () => {
  const snap = snapshot([
    { key: threadAliasKey("aster vale"), site: "someforum.test",
      dir: "Jane Doe" },
  ]);
  const carried = carryReferrer({ url: "https://filehost.test/d/AbCdEf" },
    hostCapture, [forumCapture, hostCapture]);
  const payload = buildMatchPayload(
    { id: 1, url: "https://filehost.test/d/AbCdEf" }, hostCapture, carried);
  const out = localDecide(localContext(payload), snap);
  assert.equal(out.dir, "Jane Doe");
  assert.equal(out.auto, true);
});

test("no carry means the payload says so, rather than guessing", () => {
  const payload = buildMatchPayload({ id: 1, url: "https://filehost.test/d/x" },
    hostCapture, null);
  assert.equal(payload.page.referrerUrl, "");
  assert.equal(payload.page.referrerTitle, "");
});
