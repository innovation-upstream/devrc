// Identity signals in the extension, and the auto-file gate in the cached
// fallback. Both must agree with the sidecar: the fallback runs exactly when
// the sidecar is unreachable, so any divergence is invisible until it misfiles.
//
// The URL table is shared with tests/test_signals.py -- see fixtures/url_cases.json.
import test from "node:test";
import assert from "node:assert/strict";

import {
  KIND_CATEGORY, KIND_PERFORMER, SCORE_ALIAS_SITE, buildMatchPayload,
  carryReferrer, discordAliasKey, discordChannelId, discordSourceKey, hostOf,
  identitySignals,
  kindOf,
  localContext, localDecide, originalFromPreview, preferOriginalUrl,
  subjectPhrases, threadAliasKey,
  threadSlug,
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

test("every table row's ledger key is signature-free and host-folded", () => {
  // Driven off the SAME table as the channel ids, so a URL shape added there
  // is covered here too rather than needing a second hand-copied list -- the
  // exact failure the table's own `_why` block describes.
  //
  // 🔴 IT ASSERTS PROPERTIES, NOT A RE-DERIVED STRING. This used to assert
  // `=== c.url.split("?")[0]`, which is not the contract: the key also drops
  // the fragment, lower-cases the host and folds the proxy host to the origin.
  // A legitimate new fixture row carrying a `#fragment` therefore RED-ed this
  // guard while the implementation was correct -- a guard that punishes the
  // fixture growth it advertises. Exact values are pinned by the hand-written
  // literal tests below, which is where an exact expectation belongs.
  for (const c of URL_CASES.discord) {
    const key = discordSourceKey(c.url);
    if (!c.channel) {
      assert.equal(key, "", c.url);
      continue;
    }
    assert.equal(key.includes("?"), false, `query survived: ${c.url}`);
    assert.equal(key.includes("#"), false, `fragment survived: ${c.url}`);
    assert.ok(key.startsWith("https://cdn.discordapp.com/"),
      `host not folded to the origin: ${c.url} -> ${key}`);
    assert.equal(key.endsWith(new URL(c.url).pathname), true,
      `path not preserved: ${c.url} -> ${key}`);
  }
});

test("the proxy copy and the original are ONE ledger row", () => {
  // The failure this fold exists to remove: save an image once from Chrome's
  // own "Save image as…" (which yields the proxy src) and once from this
  // extension's menu (which yields the origin), and a host-scoped key files
  // them as two assets that never accumulate a hit.
  const path = `/attachments/${CHANNEL}/998877665544332211/a.png`;
  const proxy = `https://media.discordapp.net${path}?format=webp&width=550`;
  const origin = `https://cdn.discordapp.com${path}?ex=1&is=2&hm=3`;
  assert.equal(discordSourceKey(proxy), discordSourceKey(origin));
  assert.equal(discordSourceKey(proxy), `https://cdn.discordapp.com${path}`);
});

test("a fragment never reaches the ledger key", () => {
  // The shape that RED-ed the old table guard. Pinned so it cannot come back.
  assert.equal(discordSourceKey(`${CDN}#t=5`), CDN);
});

// --- Discord ---------------------------------------------------------------- //
test("the signature does not reach the ledger key", () => {
  // Discord re-signs on every page load, so these two name ONE asset. Keyed on
  // the raw URL they are two ledger rows and `have` can only ever miss.
  const a = `${CDN}?ex=68b1&is=68b0&hm=aaaaaaaa`;
  const b = `${CDN}?ex=68c2&is=68c1&hm=bbbbbbbb`;
  assert.notEqual(a, b);
  assert.equal(discordSourceKey(a), discordSourceKey(b));
  assert.equal(discordSourceKey(a), CDN);
});

test("two different attachments in one channel keep different ledger keys", () => {
  // The failure mode of over-normalising: a channel-wide key would collapse
  // every attachment ever posted into one row.
  const other
    = `https://cdn.discordapp.com/attachments/${CHANNEL}/111111111111111111/b.mp4`;
  assert.notEqual(discordSourceKey(CDN), discordSourceKey(other));
});

test("a non-attachment Discord URL gets no ledger key", () => {
  assert.equal(
    discordSourceKey(`https://cdn.discordapp.com/avatars/${CHANNEL}/a.png`), "");
  assert.equal(discordSourceKey("https://example-site.test/a.mp4"), "");
  assert.equal(discordSourceKey(""), "");
});

test("a Discord attachment's key BEATS the capture's own embed-page key", () => {
  // A Discord channel URL names thousands of assets. If the capture's key won,
  // every attachment in the channel would share one ledger row.
  const payload = buildMatchPayload(
    { id: 1, url: CDN },
    { sourceKey: "https://discord.com/channels/1/2" },
    null);
  assert.equal(payload.sourceKey, CDN);
});

test("a non-Discord download still uses the capture's key", () => {
  const payload = buildMatchPayload(
    { id: 1, url: "https://cdn.example-site.test/signed.mp4?token=x" },
    { sourceKey: "https://example-embed.test/e/abc" },
    null);
  assert.equal(payload.sourceKey, "https://example-embed.test/e/abc");
});

test("an ordinary download still carries no key at all", () => {
  const payload = buildMatchPayload(
    { id: 1, url: "https://example-site.test/a.mp4" }, {}, null);
  assert.equal(payload.sourceKey, "");
});

// --- preferring the original over the resizing proxy ------------------------ //
const PREVIEW
  = `https://media.discordapp.net/attachments/${CHANNEL}/998877665544332211/a.png`;
const ORIGINAL
  = `https://cdn.discordapp.com/attachments/${CHANNEL}/998877665544332211/a.png`;

test("a proxy thumbnail is swapped for the original behind it", () => {
  assert.equal(
    preferOriginalUrl(`${PREVIEW}?format=webp&width=550&height=733`,
      `${ORIGINAL}?ex=1&is=2&hm=3`),
    `${ORIGINAL}?ex=1&is=2&hm=3`);
});

test("a link to a DIFFERENT asset is never substituted", () => {
  // The whole point of comparing paths: a link that merely wraps an image is
  // not evidence that it is the same thing.
  //
  // The result is the CLICKED image's own original, not the link's asset and
  // not the clicked thumbnail either. This is the live mosaic shape -- a
  // multi-image message, where the only anchor on offer belongs to a sibling
  // image -- so `z.png` must not win, and `a.png`'s thumbnail is not the best
  // answer available for `a.png`.
  const elsewhere
    = `https://cdn.discordapp.com/attachments/${CHANNEL}/222222222222222222/z.png`;
  assert.equal(preferOriginalUrl(PREVIEW, elsewhere), ORIGINAL);
});

test("a non-Discord pair is left exactly alone", () => {
  assert.equal(
    preferOriginalUrl("https://example-site.test/thumb.jpg",
      "https://example-site.test/full.jpg"),
    "https://example-site.test/thumb.jpg");
});

test("an avatar sharing the hosts is never swapped", () => {
  const av = `https://media.discordapp.net/avatars/${CHANNEL}/a.png`;
  const av2 = `https://cdn.discordapp.com/avatars/${CHANNEL}/a.png`;
  assert.equal(preferOriginalUrl(av, av2), av);
});

test("a video's own src survives -- there is nothing better to swap to", () => {
  assert.equal(preferOriginalUrl(CDN, "https://discord.com/channels/1/2"), CDN);
});

test("either URL missing degrades to the other, never to empty", () => {
  assert.equal(preferOriginalUrl("", ORIGINAL), ORIGINAL);
  // No link: the rewrite is the whole answer, and it is the ORIGINAL. This
  // assertion used to read `PREVIEW`, which was correct about the code and
  // wrong about production -- see the next block.
  assert.equal(preferOriginalUrl(PREVIEW, ""), ORIGINAL);
  assert.equal(preferOriginalUrl("", ""), "");
});

// --- the seam: Chrome never supplies `linkUrl` on a Discord image ----------- //
// MEASURED 2026-09-03 against the live client, 3 attachments / 2 channels / 2
// message shapes: an image attachment has ZERO ancestor <a> elements, so
// `info.linkUrl` is absent on every real right-click and the swap above cannot
// fire. These pin the branch that ACTUALLY runs. Red before the rewrite
// landed: the pre-change function returned `srcUrl` here, i.e. the thumbnail.

test("with no link at all, a proxy attachment still yields the original", () => {
  assert.equal(
    preferOriginalUrl(`${PREVIEW}?ex=1&is=2&hm=3&format=webp&width=550&height=733`
      + "&quality=lossless", ""),
    `${ORIGINAL}?ex=1&is=2&hm=3`);
});

test("the signature is carried across the host swap, the resize knobs are not",
  () => {
    // Not cosmetic: `ex`/`is`/`hm` authorise the fetch. Measured identical on
    // both hosts for one asset; stripping them was the negative control and it
    // failed to load.
    const out = new URL(originalFromPreview(
      `${PREVIEW}?ex=aa&is=bb&hm=cc&format=webp&width=550`));
    assert.equal(out.host, "cdn.discordapp.com");
    assert.deepEqual([...out.searchParams.keys()].sort(), ["ex", "hm", "is"]);
    assert.equal(out.searchParams.get("hm"), "cc");
  });

test("a stray empty-named parameter is dropped too", () => {
  // A live proxy URL carried one (Discord emits a bare `&`). The measured
  // rewrite dropped it, so this keeps the output byte-identical to what was
  // probed rather than merely equivalent.
  assert.equal(originalFromPreview(`${PREVIEW}?&ex=1&width=550`),
    `${ORIGINAL}?ex=1`);
});

test("an avatar with no link is left alone -- the guard is REACHABLE", () => {
  // The rewrite must not fire on every proxy-host URL, and this case reaches
  // the guard by a path no earlier check rejects: `linkUrl` absent, proxy
  // host, but not an attachment.
  const av = `https://media.discordapp.net/avatars/${CHANNEL}/a.png?size=128`;
  assert.equal(preferOriginalUrl(av, ""), av);
  assert.equal(originalFromPreview(av), av);
});

test("originalFromPreview passes through anything it does not own", () => {
  assert.equal(originalFromPreview("https://example-site.test/a.png?width=1"),
    "https://example-site.test/a.png?width=1");
  assert.equal(originalFromPreview(ORIGINAL), ORIGINAL);
  assert.equal(originalFromPreview("not a url"), "not a url");
  assert.equal(originalFromPreview(""), "");
});

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
