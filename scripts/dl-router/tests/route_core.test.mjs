// The pure routing core: the three-tier context-correlation ladder, the cached
// fallback matcher, and the onDeterminingFilename suggest() ladder.
//
// The single most important property asserted here: suggest() is called EXACTLY
// ONCE on every path — sidecar answer, sidecar timeout, sidecar error, no cache,
// unsafe directory, hostile filename, and every race between them. Chrome
// silently discards a late suggestion and a listener that never calls it hangs
// the download, so "exactly once, always" is the contract.
import test from "node:test";
import assert from "node:assert/strict";

import {
  SCORE_ALIAS_GLOBAL, SCORE_ALIAS_SITE, SCORE_CONTAIN_MAX, SCORE_CONTAIN_MIN,
  SCORE_TAG_EXACT, buildMatchPayload, contentTokens, correlateCapture,
  formatDup, handleDetermining, localDecide, normKey, passesFuzzyGuard,
  subjectPhrases,
} from "../extension/route_core.js";

const DIRS = [
  { name: "Jane Doe", key: "janedoe", tokens: ["jane", "doe"] },
  { name: "john-smith", key: "johnsmith", tokens: ["john", "smith"] },
  { name: "Mary_Major", key: "marymajor", tokens: ["mary", "major"] },
  { name: "other", key: "other", tokens: [] },
];

const SNAPSHOT = { dirs: DIRS, aliases: [], threshold: 0.75, otherDir: "other" };

/** A suggest() spy that records every call. */
function spy() {
  const calls = [];
  const fn = (arg) => calls.push(arg);
  fn.calls = calls;
  return fn;
}

/** Deterministic timers the test drives by hand. */
function fakeTimers() {
  let seq = 0;
  const pending = new Map();
  return {
    setTimeout: (fn, ms) => { const id = ++seq; pending.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => pending.delete(id),
    fire: () => {
      const entries = [...pending.entries()];
      pending.clear();
      for (const [, { fn }] of entries) fn();
    },
    get size() { return pending.size; },
  };
}

const settle = () => new Promise((r) => setImmediate(r));

// --- normalisation ---------------------------------------------------------- //
test("normKey folds all three naming conventions", () => {
  for (const v of ["Jane Doe", "jane-doe", "Jane_Doe", "JANE.DOE", " Jane  Doe "]) {
    assert.equal(normKey(v), "janedoe", v);
  }
});

test("normKey strips diacritics and compatibility forms", () => {
  assert.equal(normKey("José"), "jose");
  assert.equal(normKey("Ｊａｎｅ"), "jane");
  assert.equal(normKey(null), "");
});

test("contentTokens drops stopwords", () => {
  assert.deepEqual(contentTokens("The Jane Doe video"), ["jane", "doe"]);
});

test("the fuzzy guard needs two tokens or one long one", () => {
  assert.equal(passesFuzzyGuard(["abc"]), false);
  assert.equal(passesFuzzyGuard(["abcd"]), true);
  assert.equal(passesFuzzyGuard(["ab", "cd"]), true);
  assert.equal(passesFuzzyGuard([]), false);
});

// --- context correlation ---------------------------------------------------- //
test("tier 1: exact URL match on url or finalUrl", () => {
  const captures = [
    { href: "https://example-site.test/other", pageUrl: "p", ts: 100 },
    { href: "https://example-site.test/file.mp4", pageUrl: "p2", ts: 50 },
  ];
  const out = correlateCapture(
    { url: "https://example-site.test/file.mp4" }, captures,
    { now: 200, activeTabId: 9 });
  assert.equal(out.tier, 1);
  assert.equal(out.capture.pageUrl, "p2");
});

test("tier 1 also matches a captured media source", () => {
  const captures = [{ mediaSrc: "https://example-site.test/v.mp4", ts: 10 }];
  const out = correlateCapture(
    { finalUrl: "https://example-site.test/v.mp4" }, captures, { now: 20 });
  assert.equal(out.tier, 1);
});

test("tier 2: referrer matches a captured page URL", () => {
  const captures = [{ href: "x", pageUrl: "https://example-site.test/v/1", ts: 5 }];
  const out = correlateCapture(
    { url: "https://cdn.example-site.test/opaque", referrer: "https://example-site.test/v/1" },
    captures, { now: 10 });
  assert.equal(out.tier, 2);
});

test("tier 3: most recent capture on the active tab inside the window", () => {
  const captures = [
    { pageUrl: "old", tabId: 7, ts: 1000 },
    { pageUrl: "new", tabId: 7, ts: 9000 },
    { pageUrl: "other-tab", tabId: 8, ts: 9500 },
  ];
  const out = correlateCapture({ url: "https://cdn.test/x" }, captures,
    { now: 10000, windowMs: 15000, activeTabId: 7 });
  assert.equal(out.tier, 3);
  assert.equal(out.capture.pageUrl, "new");
});

test("tier 3 respects the time window", () => {
  const captures = [{ pageUrl: "stale", tabId: 7, ts: 1000 }];
  const out = correlateCapture({ url: "x" }, captures,
    { now: 100000, windowMs: 15000, activeTabId: 7 });
  assert.equal(out.tier, 0);
  assert.equal(out.capture, null);
});

test("tiers are ordered: an exact URL beats a newer active-tab capture", () => {
  const captures = [
    { pageUrl: "recent", tabId: 7, ts: 9999 },
    { href: "https://example-site.test/f.mp4", pageUrl: "exact", ts: 1 },
  ];
  const out = correlateCapture({ url: "https://example-site.test/f.mp4" },
    captures, { now: 10000, windowMs: 15000, activeTabId: 7 });
  assert.equal(out.tier, 1);
  assert.equal(out.capture.pageUrl, "exact");
});

test("no captures correlates to nothing", () => {
  assert.deepEqual(correlateCapture({ url: "x" }, [], { now: 1 }),
    { capture: null, tier: 0 });
  assert.deepEqual(correlateCapture({}, null, { now: 1 }),
    { capture: null, tier: 0 });
});

test("a missing activeTabId does not crash tier 3", () => {
  const out = correlateCapture({ url: "x" }, [{ pageUrl: "p", ts: 1 }],
    { now: 2 });
  assert.equal(out.tier, 0);
});

// --- cached fallback matcher ------------------------------------------------ //
test("cached decision: exact tag match", () => {
  const out = localDecide({ tags: ["jane-doe"] }, SNAPSHOT);
  assert.equal(out.dir, "Jane Doe");
  assert.equal(out.confidence, SCORE_TAG_EXACT);
  assert.equal(out.auto, true);
  assert.equal(out.source, "cache");
});

test("cached decision: site alias outranks the global one", () => {
  const snap = {
    ...SNAPSHOT,
    aliases: [
      { key: "jd", site: "", dir: "john-smith" },
      { key: "jd", site: "example-site.test", dir: "Jane Doe" },
    ],
  };
  const out = localDecide({ tags: ["JD"], site: "example-site.test" }, snap);
  assert.equal(out.dir, "Jane Doe");
  assert.equal(out.confidence, SCORE_ALIAS_SITE);
});

test("cached decision: global alias applies on any site", () => {
  const snap = { ...SNAPSHOT, aliases: [{ key: "jd", site: "", dir: "Jane Doe" }] };
  const out = localDecide({ tags: ["JD"], site: "elsewhere.test" }, snap);
  assert.equal(out.confidence, SCORE_ALIAS_GLOBAL);
});

test("cached decision: containment is scored between the bounds", () => {
  const out = localDecide({ pageTitle: "Jane Doe and Mary Major" }, SNAPSHOT);
  assert.ok(out.confidence >= SCORE_CONTAIN_MIN);
  assert.ok(out.confidence <= SCORE_CONTAIN_MAX);
  assert.equal(out.auto, false, "below threshold -> not auto");
});

test("cached decision: a short directory name does not match prose", () => {
  const snap = {
    ...SNAPSHOT,
    dirs: [{ name: "Cat", key: "cat", tokens: ["cat"] }, DIRS[3]],
  };
  assert.equal(localDecide({ pageTitle: "the cat sat on a mat" }, snap), null);
});

test("cached decision returns null without a usable snapshot", () => {
  assert.equal(localDecide({ tags: ["Jane Doe"] }, null), null);
  assert.equal(localDecide({ tags: ["Jane Doe"] }, { dirs: [] }), null);
  assert.equal(localDecide({}, SNAPSHOT), null);
});

test("cached decision never names a directory outside the snapshot", () => {
  const snap = { ...SNAPSHOT, aliases: [{ key: "jd", site: "", dir: "Ghost Dir" }] };
  assert.equal(localDecide({ tags: ["JD"] }, snap), null);
});

test("subjectPhrases orders tags first and dedupes by key", () => {
  const phrases = subjectPhrases({
    tags: ["Jane Doe", "jane-doe"], pageTitle: "Jane Doe", linkText: "Mary Major",
  });
  assert.equal(phrases[0], "Jane Doe");
  assert.ok(phrases.includes("Mary Major"));
  assert.equal(phrases.filter((p) => normKey(p) === "janedoe").length, 1);
});

// --- payload + dedupe formatting -------------------------------------------- //
test("buildMatchPayload takes only the filename's base component", () => {
  const payload = buildMatchPayload(
    { url: "u", finalUrl: "f", referrer: "r", filename: "/abs/path/clip.mp4",
      mime: "video/mp4", fileSize: 42 },
    { pageTitle: "T", pageUrl: "P", site: "s", tags: ["a"], og: { title: "x" },
      linkText: "L", alt: "A" });
  assert.equal(payload.filename, "clip.mp4");
  assert.equal(payload.size, 42);
  assert.deepEqual(payload.page.tags, ["a"]);
});

test("buildMatchPayload tolerates a missing capture", () => {
  const payload = buildMatchPayload({ filename: "x.mp4" }, null);
  assert.deepEqual(payload.page.tags, []);
  assert.equal(payload.page.title, "");
});

test("formatDup renders both duplicate scopes and nothing otherwise", () => {
  assert.match(formatDup({ where: "target-dir", relpath: "Jane Doe/a.mp4", kind: "name+size" }),
    /already in this folder/);
  assert.match(formatDup({ where: "library", relpath: "john-smith/a.mp4", kind: "name" }),
    /already in the library/);
  assert.equal(formatDup(null), null);
  assert.equal(formatDup({}), null);
});

// --- the suggest() ladder --------------------------------------------------- //
const ITEM = { id: 1, filename: "clip.mp4", url: "https://example-site.test/f" };

function ladderDeps(overrides = {}) {
  return {
    knownDirs: new Set(["Jane Doe", "john-smith"]),
    otherDir: "other",
    timeoutMs: 400,
    localDecision: () => null,
    requestMatch: () => Promise.resolve(null),
    ...overrides,
  };
}

test("sidecar answers in time: its directory is used, suggest called once", async () => {
  const suggest = spy();
  const timers = fakeTimers();
  const ret = handleDetermining(ITEM, suggest, ladderDeps({
    ...timers,
    requestMatch: () => Promise.resolve({ dir: "Jane Doe", auto: true, reason: "r" }),
  }));
  assert.equal(ret, true, "must return true for an async suggest");
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.deepEqual(suggest.calls[0],
    { filename: "Jane Doe/clip.mp4", conflictAction: "uniquify" });
  assert.equal(timers.size, 0, "the timeout timer must be cleared");
});

test("timeout with a cached decision: the cache is used, suggest called once", async () => {
  const suggest = spy();
  const timers = fakeTimers();
  let resolveLate;
  handleDetermining(ITEM, suggest, ladderDeps({
    ...timers,
    localDecision: () => ({ dir: "john-smith", auto: true, reason: "cached" }),
    requestMatch: () => new Promise((r) => { resolveLate = r; }),
  }));
  timers.fire();                       // the sidecar is too slow
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "john-smith/clip.mp4");

  resolveLate({ dir: "Jane Doe", auto: true });   // late answer must be ignored
  await settle();
  assert.equal(suggest.calls.length, 1, "a late sidecar answer must not re-suggest");
});

test("timeout with no cache: falls through to the catch-all, suggest once", async () => {
  const suggest = spy();
  const timers = fakeTimers();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...timers,
    requestMatch: () => new Promise(() => {}),   // never settles
  }));
  timers.fire();
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "other/clip.mp4");
});

test("sidecar error with a cache: the cache is used, suggest once", async () => {
  const suggest = spy();
  const timers = fakeTimers();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...timers,
    localDecision: () => ({ dir: "john-smith", auto: true }),
    requestMatch: () => Promise.reject(new Error("sidecar 500")),
  }));
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "john-smith/clip.mp4");
  assert.equal(timers.size, 0);
});

test("sidecar error with no cache: catch-all, suggest once", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    requestMatch: () => Promise.reject(new Error("ECONNREFUSED")),
  }));
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "other/clip.mp4");
});

test("requestMatch throwing synchronously still suggests exactly once", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    requestMatch: () => { throw new Error("boom"); },
  }));
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "other/clip.mp4");
});

test("localDecision throwing is contained, suggest once", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    localDecision: () => { throw new Error("bad snapshot"); },
    requestMatch: () => Promise.reject(new Error("down")),
  }));
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "other/clip.mp4");
});

test("a below-threshold answer files into the catch-all, not the guess", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    requestMatch: () => Promise.resolve({ dir: "Jane Doe", auto: false }),
  }));
  await settle();
  assert.equal(suggest.calls[0].filename, "other/clip.mp4");
});

test("an unknown directory from the sidecar is refused, catch-all used", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    requestMatch: () => Promise.resolve({ dir: "Not In Snapshot", auto: true }),
  }));
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "other/clip.mp4");
});

test("a traversing directory from the sidecar is refused", async () => {
  for (const dir of ["../..", "/etc", "a/b", "..", ".", "x".repeat(200)]) {
    const suggest = spy();
    handleDetermining(ITEM, suggest, ladderDeps({
      ...fakeTimers(),
      requestMatch: () => Promise.resolve({ dir, auto: true }),
    }));
    await settle();
    assert.equal(suggest.calls.length, 1, dir);
    assert.equal(suggest.calls[0].filename, "other/clip.mp4", dir);
  }
});

test("a hostile download filename is sanitised", async () => {
  const suggest = spy();
  handleDetermining({ id: 2, filename: "/tmp/../../etc/passwd" }, suggest,
    ladderDeps({
      ...fakeTimers(),
      requestMatch: () => Promise.resolve({ dir: "Jane Doe", auto: true }),
    }));
  await settle();
  assert.equal(suggest.calls[0].filename, "Jane Doe/passwd");
});

test("conflictAction is always uniquify, never overwrite", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    requestMatch: () => Promise.resolve({ dir: "Jane Doe", auto: true }),
  }));
  await settle();
  assert.equal(suggest.calls[0].conflictAction, "uniquify");
});

test("the catch-all is usable even when it is not in knownDirs", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    knownDirs: new Set(["Jane Doe"]),
    otherDir: "catch-all",
    requestMatch: () => Promise.resolve(null),
  }));
  await settle();
  assert.equal(suggest.calls[0].filename, "catch-all/clip.mp4");
});

test("onDecision receives the decision and cannot break the download", async () => {
  const seen = [];
  const suggest = spy();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...fakeTimers(),
    requestMatch: () => Promise.resolve({ dir: "Jane Doe", auto: true, reason: "r" }),
    onDecision: (info) => { seen.push(info); throw new Error("toast exploded"); },
  }));
  await settle();
  assert.equal(suggest.calls.length, 1);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].dir, "Jane Doe");
  assert.equal(seen[0].source, "sidecar");
  assert.equal(seen[0].auto, true);
});

test("onDecision reports auto=false for a below-threshold answer", async () => {
  const seen = [];
  handleDetermining(ITEM, spy(), ladderDeps({
    ...fakeTimers(),
    requestMatch: () => Promise.resolve({ dir: "Jane Doe", auto: false }),
    onDecision: (info) => seen.push(info),
  }));
  await settle();
  assert.equal(seen[0].auto, false);
  assert.equal(seen[0].dir, "other");
});

test("timer firing after the sidecar answered cannot double-suggest", async () => {
  const suggest = spy();
  const timers = fakeTimers();
  handleDetermining(ITEM, suggest, ladderDeps({
    ...timers,
    requestMatch: () => Promise.resolve({ dir: "Jane Doe", auto: true }),
  }));
  await settle();
  timers.fire();          // a stale timer, if any survived
  await settle();
  assert.equal(suggest.calls.length, 1);
});

test("default timers are used when none are injected", async () => {
  const suggest = spy();
  handleDetermining(ITEM, suggest, {
    knownDirs: new Set(["Jane Doe"]),
    otherDir: "other",
    timeoutMs: 5,
    localDecision: () => null,
    requestMatch: () => new Promise(() => {}),
  });
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "other/clip.mp4");
});
