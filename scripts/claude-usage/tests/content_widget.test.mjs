// content_widget.test.mjs -- DOM coverage for the injected widget's PAINTER.
//
// 🔴 WHY THIS FILE EXISTS. content_widget.js is the largest file in this
// feature, it executes inside the operator's real logged-in claude.ai tab, and
// it had ZERO tests. Two consecutive audit rounds then found bugs in it and
// nowhere else:
//
//   round 1  the widget never mounted at all (lib/severity.js missing from
//            web_accessible_resources, the rejection swallowed by a catch)
//   round 2  the record tone was rendered NOWHERE on the expanded card, so a
//            `severity: "critical"` account showed a red badge above three
//            green bars; and retiring a COLLAPSED widget re-expanded it into a
//            232px box over claude.ai's composer corner
//
// 🔴 AND THE FIRST GUARD WRITTEN FOR ROUND 2's FINDING DID NOT CATCH IT. It
// asserted `widgetModel(...).tone === "crit"` -- which was ALREADY TRUE at the
// broken tip, because the model was right and the PAINTER was what dropped the
// value. Watched green at the pre-fix commit. A model-level assertion named
// after the card is a guard whose description claims coverage it does not
// provide, which is worse than none because it stops the next person looking.
// The defects live in the DOM, so the guard has to live in the DOM.
//
// The harness is scripts/discord-embed-ext/tests/fake_discord_dom.mjs, reused
// rather than copied -- it is the repo's shadow-DOM content-script fixture and
// its comments record several traps (uppercase tagName, nodeType, computed
// style priority) that a hand-rolled fake would re-introduce.
import test from "node:test";
import assert from "node:assert/strict";

const { FakeElement, FakeComputedStyle } =
  await import("../../discord-embed-ext/tests/fake_discord_dom.mjs");

// 🔴 TWO HARNESS EXTENSIONS, AND BOTH FAILED SILENTLY BEFORE THEY EXISTED.
//
// (1) FakeComputedStyle declares `get cssText` with NO setter. content_widget
//     sets `host.style.cssText = "..."` in one shot, and assigning to a
//     getter-only property THROWS in an ES module (strict mode). That throw
//     happened inside the promise chain in render(), whose `.catch` swallowed
//     it — so the widget simply never mounted and every assertion failed with
//     "no host element was appended", pointing at the code under test rather
//     than at the fixture. A fixture that cannot model an API the code uses
//     produces a red that reads exactly like a real defect.
// (2) FakeElement has no `id` property handling; the code assigns `el.id`
//     directly rather than via setAttribute.
//
// Extended here rather than upstream: these are claude-usage's needs, and
// discord-embed's own suite pins the current behaviour.
if (!Object.getOwnPropertyDescriptor(FakeComputedStyle.prototype, "cssText").set) {
  const desc = Object.getOwnPropertyDescriptor(FakeComputedStyle.prototype, "cssText");
  Object.defineProperty(FakeComputedStyle.prototype, "cssText", {
    get: desc.get,
    set(v) {
      for (const decl of String(v).split(";")) {
        const i = decl.indexOf(":");
        if (i < 0) continue;
        this.setProperty(decl.slice(0, i).trim(), decl.slice(i + 1).trim());
      }
    },
    configurable: true,
  });
}
Object.defineProperty(FakeElement.prototype, "id", {
  get() { return this.attrs.id || ""; },
  set(v) { this.attrs.id = String(v); },
  configurable: true,
});
// (3) FakeElement implements appendChild but NOT the variadic `append`, which
//     paintExpanded uses throughout. Calling an undefined method threw inside
//     the same swallowed promise chain, so the card was left half-built: the
//     header, rows and bars were all missing while the `retire()` path — which
//     happens to use appendChild — rendered fine. The symptom was "the card
//     header has no tone indicator", i.e. it looked exactly like the
//     regression under test still being present. THAT is why a fixture gap and
//     a real defect have to be told apart before a red is believed.
if (typeof FakeElement.prototype.append !== "function") {
  FakeElement.prototype.append = function (...nodes) {
    for (const n of nodes) this.appendChild(n);
  };
}
// No NOW here on purpose: every fixture below is built relative to
// `Date.now()`, because render() reads the real clock.
const { ORG_A, ORG_B, ORG_C, ORG_D, NAME_A, NAME_B, NAME_C, NAME_D, fullUsage } =
  await import("./fixtures.mjs");
const { normalizeUsage } = await import("../extension/lib/normalize.js");

const WIDGET_URL = new URL("../extension/lib/widget.js", import.meta.url).href;

// The module is imported ONCE with autostart suppressed, then driven through
// its test surface. A cache-busting query does NOT work here: node's ESM cache
// does not re-evaluate a module per query string (measured — only the first
// mount ran, and every later test saw an empty page), which is exactly why
// content_widget.js carries a NO_AUTOSTART hook like its two siblings.
globalThis.CLAUDE_USAGE_WIDGET_NO_AUTOSTART = true;

/** A fresh page + extension context, booted against them. */
async function mount(opts) {
  const o = opts || {};
  const root = new FakeElement("html");
  root._isConnected = true;

  const timers = [];
  const store = Object.assign({ accounts: {}, lastActiveOrg: null }, o.storage || {});
  const changedListeners = [];

  globalThis.document = {
    documentElement: root,
    createElement: (t) => new FakeElement(t),
    createElementNS: (_ns, t) => new FakeElement(t),
    getElementById: (id) => {
      const hit = root.children.filter((c) => c.attrs && c.attrs.id === id);
      return hit.length ? hit[0] : null;
    },
  };
  globalThis.chrome = {
    runtime: {
      id: o.deadContext ? undefined : "test-extension-id",
      getURL: () => WIDGET_URL,
    },
    storage: {
      local: { get: async () => ({ ...store }), set: async (p) => Object.assign(store, p) },
      onChanged: { addListener: (fn) => changedListeners.push(fn) },
    },
  };
  globalThis.setInterval = (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length; };
  globalThis.setTimeout = setTimeout;
  globalThis.clearInterval = (h) => { if (timers[h - 1]) timers[h - 1].cleared = true; };
  globalThis.console = Object.assign({}, console, { warn: (...a) => warnings.push(a.join(" ")) });
  const warnings = [];

  await import("../extension/content_widget.js");
  const W = globalThis.__CU_WIDGET__;
  W.reset();                       // drop the previous test's page handles
  W.boot();
  // Let boot()'s dynamic import and the storage read settle.
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  for (let i = 0; i < 20; i += 1) await Promise.resolve();

  const host = root.children.find((c) => c.attrs && c.attrs.id === "claude-usage-tracker-widget");
  return {
    root, host, shadow: host && host._shadowRoot, timers, warnings, store,
    changedListeners, W,
  };
}

const rec = (over) => {
  const r = normalizeUsage(fullUsage(), ORG_A, NAME_A, Date.now());
  if (over) over(r);
  return r;
};
const cls = (el) => (el && el.attrs ? el.attrs.class || "" : "");
const find = (shadow, sel) => (shadow ? shadow.querySelector(sel) : null);

// --- the mount itself ------------------------------------------------------- //

test("the widget mounts a shadow host on documentElement", async () => {
  const m = await mount({ storage: { accounts: { [ORG_A]: rec() }, lastActiveOrg: ORG_A } });
  assert.ok(m.host, "no host element was appended");
  assert.ok(m.shadow, "host has no shadow root");
  // React owns <body>; the host must not be there to be taken with a re-render.
  assert.equal(m.host.parentElement, m.root);
});

test("🔴 the host does not take clicks; only the drawn card does", async () => {
  // The host is pinned over claude.ai's bottom-right, where its composer
  // controls sit on a tiled window. pointer-events must be none on the host
  // and auto only on what is actually painted.
  const m = await mount({ storage: { accounts: { [ORG_A]: rec() }, lastActiveOrg: ORG_A } });
  assert.equal(m.host.style.getPropertyValue("pointer-events"), "none",
    "the host takes clicks that miss the drawn card");
});

// --- 🔴 the round-2 regression, pinned WHERE IT LIVES ------------------------ //

test("🔴 the expanded card RENDERS the record tone, not just the per-window bars", async () => {
  // severity "critical" with both windows calm: every bar is legitimately ok,
  // so if the painter drops model.tone the card shows nothing alarming at all
  // while the toolbar badge is red. That was the shipped state.
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: rec((r) => {
          r.severity = "critical";
          r.session.utilization = 5;
          r.weekly.utilization = 5;
        }),
      },
      lastActiveOrg: ORG_A,
    },
  });
  const dot = find(m.shadow, ".dot");
  assert.ok(dot, "the card header has no tone indicator — the record tone is unrendered");
  assert.match(cls(dot), /\bt-crit\b/,
    `header dot is "${cls(dot)}" — the API's critical severity never reached the card`);

  // ...and the per-window bars stay honest at the same time. Both halves, or
  // a fix that simply reverts to painting everything with the record tone
  // would pass.
  const fills = m.shadow.querySelectorAll(".fill");
  assert.ok(fills.length >= 2, `only ${fills.length} bars rendered`);
  for (const f of fills) {
    assert.match(cls(f), /\bt-ok\b/,
      `a calm window rendered as "${cls(f)}" — the bar is misreporting itself`);
  }
});

test("a per-window bar is coloured by its OWN window", async () => {
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: rec((r) => {
          r.severity = null;
          r.session.utilization = 5;
          r.weekly.utilization = 97;
        }),
      },
      lastActiveOrg: ORG_A,
    },
  });
  const fills = m.shadow.querySelectorAll(".fill");
  assert.match(cls(fills[0]), /\bt-ok\b/, "5% session bar must not be red");
  assert.match(cls(fills[1]), /\bt-crit\b/, "97% weekly bar must be red");
});

// --- 🔴 the other round-2 regression ---------------------------------------- //

test("🔴 retiring a COLLAPSED widget does not re-expand it over the composer", async () => {
  // The operator collapsed it deliberately to clear that corner. An extension
  // reload must not hand the corner back as an undismissable 232px card.
  const m = await mount({
    storage: {
      accounts: { [ORG_A]: rec() }, lastActiveOrg: ORG_A, widgetCollapsed: true,
    },
  });
  assert.ok(find(m.shadow, ".pill"), "precondition: it starts collapsed");

  // Kill the extension context the way a reload does, then fire the tick.
  globalThis.chrome.runtime.id = undefined;
  const tick = m.timers.find((t) => !t.cleared);
  assert.ok(tick, "no tick was armed");
  tick.fn();
  for (let i = 0; i < 8; i += 1) await Promise.resolve();

  assert.equal(find(m.shadow, ".card.dead"), null,
    "a collapsed widget was re-expanded into a card on retirement");
  assert.ok(find(m.shadow, ".pill.dead"), "expected a pill-shaped terminal state");
});

test("retiring stops the tick rather than firing forever against a dead context", async () => {
  const m = await mount({ storage: { accounts: { [ORG_A]: rec() }, lastActiveOrg: ORG_A } });
  globalThis.chrome.runtime.id = undefined;
  const tick = m.timers.find((t) => !t.cleared);
  tick.fn();
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  assert.ok(m.timers.every((t) => t.cleared), "the 30s interval is still armed");
  // And the frozen card must not keep claiming freshness.
  assert.ok(find(m.shadow, ".dead"), "no terminal state was painted");
});

// --- the empty state --------------------------------------------------------- //

test("with no stored account the card is an instruction, not zeroes", async () => {
  const m = await mount({});
  const note = find(m.shadow, ".note");
  assert.ok(note, "no waiting state rendered");
  assert.equal(m.shadow.querySelectorAll(".fill").length, 0,
    "fabricated bars before the first snapshot");
});

// --- 🔴 the other-accounts section, WHERE IT LIVES --------------------------- //
//
// The model half of this is pinned in widget.test.mjs. These are the painter's
// half, for the reason the header of this file records: round 2's finding was
// a model that was already correct and a PAINTER that dropped the value, and
// the first guard written for it passed at the broken tip because it asserted
// on the model. The defect lives in the DOM, so the guard lives in the DOM.

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

/** A stored-shaped record for a NON-active account, built through the real
 * normalizer. `resetsAtMs`/`ageH` are relative to Date.now(), which is what
 * render() reads.
 *
 * 🔴 `wk` IS A PARAMETER FOR THE REASON widget.test.mjs's twin RECORDS: every
 * fixture here used to leave `weekly.utilization` at the constant 47 that
 * `fullUsage()` supplies, on EVERY record, so this suite was structurally
 * blind to the weekly dimension -- the dimension the row rule was ignoring.
 * Values are pairwise distinct, distinct from the session percentage beside
 * them, and distinct from every constant the assertions name (80, 95, 100, 0).
 * `severity` is nulled so the percent half of the tone rule is isolated. */
function acct(uuid, name, pct, wk, resetsAtMs, ageH, over) {
  const r = normalizeUsage(fullUsage(), uuid, name, Date.now() - ageH * HOUR);
  r.severity = null;
  r.session.utilization = pct;
  r.session.resetsAt = resetsAtMs === null ? null : new Date(resetsAtMs).toISOString();
  r.weekly.utilization = wk;
  r.weekly.resetsAt = new Date(Date.now() + 4 * 24 * HOUR).toISOString();
  if (over) over(r);
  return r;
}

const textOf = (el) => (el ? el.textContent : "");

test("🔴 REGRESSION: the widget SHOWS a freed-up account, as available and not as 92%", async () => {
  // THE defect. He logs into A; B's snapshot says 91% and its five-hour window
  // closed two hours ago. B has actually freed up -- and cannot be
  // re-measured, because content_probe.js fetches with A's session cookie.
  // Before this change the widget painted A alone and the only surface
  // showing B said "Session 91% · resets soon".
  const now = Date.now();
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0),
        [ORG_B]: acct(ORG_B, NAME_B, 91, 14, now - 2 * HOUR, 6),
      },
      lastActiveOrg: ORG_A,
    },
  });

  const rows = m.shadow.querySelectorAll(".other");
  assert.equal(rows.length, 1,
    "the freed-up account is not on the card at all — the operator has to guess");
  const text = textOf(rows[0]);
  assert.match(text, /AVAILABLE/, `the row reads "${text}"`);
  assert.ok(!/resets soon/.test(text), `the row still reads "${text}"`);
  assert.match(text, new RegExp(NAME_B.replace(/[.*+?^${}()|[\]\\<>]/g, "\\$&")),
    "the row does not name the account");
  // The inference is stated and the last measured reading stays beside it.
  assert.match(text, /reset 2h ago/, text);
  assert.match(text, /was 91%/, "the last MEASURED value must remain on screen");
  assert.match(text, /measured 6h ago/, "...with its age");
});

test("🔴 REGRESSION: a weekly-BLOCKED account is not painted as the best switch target", async () => {
  // THE F1 defect, in the DOM where the operator meets it. B's five-hour
  // window reset three hours ago and its SEVEN-DAY window is at 100% and
  // locked for another four days. Watched RED at b97190c8: the row read
  // "AVAILABLE — reset 3h ago (was 95%, measured 8h ago)" with a green dot,
  // above a usable account.
  const now = Date.now();
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0),
        [ORG_B]: acct(ORG_B, NAME_B, 95, 100, now - 3 * HOUR, 8, (r) => {
          r.weekly.lockedReason = "Weekly limit reached.";
        }),
        [ORG_C]: acct(ORG_C, NAME_C, 12, 19, now + 2 * HOUR, 1),
      },
      lastActiveOrg: ORG_A,
    },
  });

  const rows = m.shadow.querySelectorAll(".other");
  assert.equal(rows.length, 2);
  const blockedRow = find(m.shadow, ".other.blocked");
  assert.ok(blockedRow, "no blocked row was painted — the state never reaches the DOM");
  const text = textOf(blockedRow);
  assert.ok(!/AVAILABLE/.test(text), `the blocked row still reads "${text}"`);
  assert.match(text, /BLOCKED/, text);
  // 🔴 The LOCK the active card has always rendered must reach an other row.
  assert.match(text, /Weekly limit reached\./,
    `the lock is dropped on the way to an other-account row: "${text}"`);
  // The SHAPE, not the exact string: this harness runs on the real clock, so
  // the fixture's `now + 4d` has already drifted to 3d23h by the time
  // render() reads Date.now(). The exact text is pinned in widget.test.mjs,
  // which uses a frozen NOW. What matters here is that a MULTI-DAY wait
  // reaches the DOM at all.
  assert.match(text, /frees up in \d+d\d+h/, `no wait time on screen: "${text}"`);
  assert.match(text, /session was 95%/, "the last MEASURED value must remain on screen");

  // Coloured as unusable, and NOT greyed — the verdict is a live inference.
  assert.match(cls(find(m.shadow, ".other.blocked .dot")), /\bt-crit\b/,
    "an unusable account is painted calm");
  assert.ok(!/\bstale\b/.test(cls(blockedRow)),
    `the blocked row is class "${cls(blockedRow)}" — greyed out`);

  // ...and it is painted BELOW the account that actually works.
  assert.match(textOf(rows[0]), new RegExp(NAME_C.replace(/[.*+?^${}()|[\]\\<>]/g, "\\$&")),
    "the account that rejects you at the login screen is the first row");
});

test("🔴 the presumed-free row is NOT painted as stale", async () => {
  // A 9h-old snapshot is stale by the 6h rule, and the free row is stale BY
  // CONSTRUCTION. Greying it washes out the most actionable thing on the card.
  const now = Date.now();
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0),
        [ORG_B]: acct(ORG_B, NAME_B, 91, 14, now - 2 * HOUR, 9),
        [ORG_C]: acct(ORG_C, NAME_C, 62, 19, now + 4 * HOUR, 9),
      },
      lastActiveOrg: ORG_A,
    },
  });
  const freeRow = find(m.shadow, ".other.free");
  assert.ok(freeRow, "no presumed-free row was painted");
  assert.ok(!/\bstale\b/.test(cls(freeRow)),
    `the free row is class "${cls(freeRow)}" — greyed out`);
  assert.match(cls(find(m.shadow, ".other.free .dot")), /\bt-ok\b/,
    "the free row lost its colour");

  const measuredRow = find(m.shadow, ".other.measured");
  assert.ok(measuredRow, "no measured row was painted");
  assert.match(cls(measuredRow), /\bstale\b/,
    "a stale MEASURED row must still grey — there the percentage IS the claim");
});

/**
 * Every CSS rule in a `var X = [ "...", "..." ].join("")` block, as
 * `{selectors: [...], body}`. Parses the STRUCTURE rather than matching a
 * spelling, because a selector list is what the old guard could not see.
 *
 * `@media` preludes are stripped first so the rules nested inside them are
 * tokenized like any others -- a hazard re-introduced inside the dark-mode
 * block would otherwise be invisible.
 *
 * ⚠ IT ONLY SEES DOUBLE-QUOTED FRAGMENTS. Single quotes and template literals
 * are not matched, so CSS written either way is invisible to this parser.
 * That is adequate rather than complete: every fragment in content_widget.js
 * is a double-quoted string, and there is no build step that could rewrite
 * them. Check this function before trusting the guard below if that changes.
 */
function cssRules(src) {
  const fragments = [...src.matchAll(/"((?:[^"\\]|\\.)*)"/g)]
    .map((m) => m[1].replace(/\\(.)/g, "$1"));
  const css = fragments.join("").replace(/@media[^{]*\{/g, "");
  const out = [];
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    out.push({
      selectors: m[1].split(",").map((s) => s.trim()).filter(Boolean),
      body: m[2],
    });
  }
  return out;
}

/** Does any rule set `opacity` on `.card.stale` ITSELF (rather than on a
 * descendant of it)? A selector that continues past `.card.stale` with a
 * combinator or a space is targeting a child and is the SCOPED form. */
function dimsTheWholeCard(src) {
  return cssRules(src).filter((r) => /(^|[^-\w])opacity\s*:/.test(r.body))
    .flatMap((r) => r.selectors)
    .filter((sel) => /^(\.card\.stale|\.stale\.card)$/.test(sel));
}

test("INVARIANT GUARD: the stale dim is scoped to the active account, never to the whole card", async () => {
  // ⚠ LABELLED TWICE OVER, because it is tempting to count as regression
  // coverage and is not. It passed at b97190c8 -- the hazard is not present
  // in the source and never was; what was wrong is that the GUARD could not
  // have seen it in the shape anyone would write. So this closes a hole in a
  // guard, not a hole in the product, and the thing that demonstrates the
  // fix is the positive control below rather than a red-to-green transition.
  //
  // A SOURCE-LEVEL MECHANISM PIN, not a behavioural assertion, and labelled as
  // one: `opacity` on an ancestor cannot be undone by a descendant, so a bare
  // `.card.stale{opacity:...}` rule would grey the presumed-free row no matter
  // what class the painter gives it — and the shadow-DOM harness has no
  // cascade, so no DOM assertion above can see that. The test directly above
  // would stay green while the operator's card was uniformly washed out.
  //
  // 🔴 IT WAS A SPELLED GUARD AND IT IS NOW A STRUCTURAL ONE. The check was
  // `/"\.card\.stale\{[^"]*opacity/`, which only matches a fragment that
  // BEGINS with `.card.stale{` — so the hazard written as a selector LIST,
  // `".card.dead,.card.stale{opacity:.6}"`, walked straight past it. That is
  // not a hypothetical shape: the line directly above the scoped rule in
  // content_widget.js is `".card.dead,.pill.dead{opacity:.6}"`, so grouping
  // the two is the most natural edit anyone would make. The guard now parses
  // the rules and asks whether `.card.stale` is a selector of any rule that
  // sets opacity.
  //
  // ⚠ IT IS NOT UNDODGEABLE, AND THIS COMMENT SAID IT WAS ("which no
  // rewording can dodge"). `dimsTheWholeCard` matches the selector against
  // `/^(\.card\.stale|\.stale\.card)$/`, so it CATCHES `.card.stale{…}` and
  // any selector LIST containing it, and MISSES every equivalent written
  // differently: `.root .card.stale`, `.root>.card.stale`, `div.card.stale`,
  // `.card.stale:not(.x)` -- plus anything single-quoted or in a template
  // literal, which `cssRules` cannot see at all. Each of those greys the
  // whole card exactly as the caught form does. Practical coverage is
  // adequate because this file's CSS is entirely unprefixed, double-quoted
  // fragments with no descendant-scoped card rules; the absolute was the
  // defect, not the guard. Widening it means matching any selector whose
  // LAST compound contains both classes, which nobody has needed yet.
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../extension/content_widget.js", import.meta.url), "utf8");

  // 🔴 POSITIVE CONTROL FIRST. A detector that has never been shown to fire
  // reports a clean zero whether the hazard is absent or it is wired to
  // nothing — and this detector's predecessor WAS wired to nothing for the
  // grouped shape. Both variants must be caught, and the old regex catches
  // neither of the first two.
  const hazards = [
    '".card.dead,.card.stale{opacity:.6}"',
    '".card.stale , .other{opacity:.72}"',
    '".card.stale{opacity:.72}"',
  ];
  for (const h of hazards) {
    assert.ok(dimsTheWholeCard(`var CSS = [${h}].join("");`).length > 0,
      `the detector cannot see the hazard written as ${h}`);
  }
  // ...and a NEGATIVE control: the scoped form must NOT trip it, or the guard
  // is merely permanently red and says nothing.
  assert.deepEqual(
    dimsTheWholeCard('var CSS = [".card.stale>.head,.card.stale>.row{opacity:.72}"].join("");'),
    [], "the detector flags the SCOPED rule, so it cannot distinguish the two");

  // Only now is the real reading worth anything.
  assert.deepEqual(dimsTheWholeCard(src), [],
    "content_widget.js dims the whole card, which greys the other-account rows "
    + "including the presumed-free one");

  // And the scoped rule still has to EXIST, or the active account never greys.
  const scoped = cssRules(src).flatMap((r) => r.selectors)
    .filter((sel) => sel.startsWith(".card.stale>"));
  assert.ok(scoped.length >= 4,
    `only ${scoped.length} scoped dim selector(s) (${scoped}) — the active `
    + "account's own elements no longer grey when its snapshot is old");
});

test("🔴 the card PAINTS every account -- the '+N more' dead end is gone", async () => {
  // This test used to assert the opposite: 4 rows and a "+2 more" line. That
  // line was TERMINAL -- nothing in the card could expand it -- so on this
  // very fixture two accounts were counted and unreachable. Making the count
  // clickable would have put a button inside this section, which the
  // pointer-events test below forbids. So every row is painted and the
  // height is bounded by CSS instead.
  const now = Date.now();
  const accounts = { [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0) };
  const wks = [14, 19, 26, 33, 44, 51];
  [23, 31, 42, 53, 62, 71].forEach((p, i) => {
    const id = `9${i}999999-9999-4999-8999-999999999999`;
    accounts[id] = acct(id, `acct ${i}`, p, wks[i], now + (i + 1) * HOUR, 1);
  });
  const m = await mount({ storage: { accounts, lastActiveOrg: ORG_A } });
  assert.equal(m.shadow.querySelectorAll(".other").length, 6,
    "an account was counted instead of painted");
  const counts = m.shadow.querySelectorAll(".more")
    .map(textOf).filter((t) => /^\+\d+\s+more/.test(t));
  assert.deepEqual(counts, [], `a hidden-row count is back: ${JSON.stringify(counts)}`);
  // ...and the rows live inside the scrolling box, not loose in the section,
  // or the height bound that replaced the cap applies to nothing.
  const list = find(m.shadow, ".otherlist");
  assert.ok(list, "no .otherlist container — the card now grows without limit");
  assert.equal(list.querySelectorAll(".other").length, 6,
    "rows were painted outside the bounded container");
});

test("🔴 the height bound that replaced the cap actually scrolls", async () => {
  // A SOURCE-LEVEL MECHANISM PIN, and labelled as one for the reason the
  // `.card.stale` pin above records: the shadow-DOM harness has no cascade
  // and no layout, so no DOM assertion can see whether `.otherlist` is
  // bounded. Without both declarations the test above still passes while an
  // unbounded list covers claude.ai's composer -- which is exactly what the
  // deleted cap existed to prevent.
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../extension/content_widget.js", import.meta.url), "utf8");
  const rule = src.match(/"\.otherlist\{[^"]*"/);
  assert.ok(rule, "the .otherlist CSS rule is gone");
  assert.match(rule[0], /max-height:\s*\d+px/, `no height bound in ${rule[0]}`);
  assert.match(rule[0], /overflow-y:\s*auto/,
    `bounded but not scrollable in ${rule[0]} — rows below the bound are clipped, `
    + "which is the unreachable-row defect again with a different mechanism");
});

test("the next-free footer is painted when something is pending", async () => {
  const now = Date.now();
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 30 * 60 * 1000, 0),
        [ORG_B]: acct(ORG_B, NAME_B, 62, 14, now + HOUR + 12 * 60 * 1000, 1),
      },
      lastActiveOrg: ORG_A,
    },
  });
  assert.match(textOf(find(m.shadow, ".nextfree")), /next free: .* in 1h1[12]m/);
});

test("with a single account there is no empty 'Other accounts' heading", async () => {
  const m = await mount({
    storage: { accounts: { [ORG_A]: rec() }, lastActiveOrg: ORG_A },
  });
  assert.equal(find(m.shadow, ".others"), null, "an empty section was painted");
});

test("a per-account LABEL renames the row the widget paints", async () => {
  const now = Date.now();
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0),
        [ORG_B]: acct(ORG_B, NAME_B, 62, 14, now + HOUR, 1),
      },
      lastActiveOrg: ORG_A,
      accountLabels: { [ORG_A]: "personal", [ORG_B]: "work" },
    },
  });
  assert.equal(textOf(find(m.shadow, ".name")), "personal");
  assert.equal(textOf(find(m.shadow, ".oname")), "work");
});

test("🔴 the other-accounts section adds NO interactive target over the page", async () => {
  // Round 1 shipped a widget that swallowed clicks meant for claude.ai's
  // composer. The host stays pointer-events:none and the new rows are static
  // divs: the only clickable things in the card remain the collapse button
  // and (when collapsed) the pill. Editing a label is the POPUP's job.
  const now = Date.now();
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0),
        [ORG_B]: acct(ORG_B, NAME_B, 91, 14, now - 2 * HOUR, 6),
        [ORG_C]: acct(ORG_C, NAME_C, 62, 19, now + HOUR, 1),
      },
      lastActiveOrg: ORG_A,
    },
  });
  assert.ok(m.shadow.querySelectorAll(".other").length >= 2, "precondition: rows are painted");
  assert.equal(m.host.style.getPropertyValue("pointer-events"), "none",
    "the host takes clicks that miss the drawn card");
  assert.equal(m.shadow.querySelectorAll(".others button").length, 0,
    "a button inside the other-accounts section");
  assert.equal(m.shadow.querySelectorAll("input").length, 0,
    "a text input floating over claude.ai's composer");
  const buttons = m.shadow.querySelectorAll("button");
  assert.deepEqual(buttons.map((b) => cls(b)), ["collapse"],
    "an unexpected click target was added to the card");
});

test("collapsing still works, and the collapsed pill hides the other rows", async () => {
  const now = Date.now();
  const store = {
    accounts: {
      [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0),
      [ORG_B]: acct(ORG_B, NAME_B, 91, 14, now - 2 * HOUR, 6),
    },
    lastActiveOrg: ORG_A,
  };
  const m = await mount({ storage: store });
  assert.ok(find(m.shadow, ".other"), "precondition: expanded, with other rows");

  find(m.shadow, ".collapse").dispatchEvent({ type: "click" });
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  for (let i = 0; i < 12; i += 1) await Promise.resolve();

  assert.equal(m.store.widgetCollapsed, true, "the preference was not persisted");
  assert.ok(find(m.shadow, ".pill"), "the collapsed pill is not painted");
  assert.equal(find(m.shadow, ".other"), null,
    "the other-account rows survived the collapse");
});

test("a widget booted COLLAPSED stays collapsed even with other accounts to show", async () => {
  const now = Date.now();
  const m = await mount({
    storage: {
      accounts: {
        [ORG_A]: acct(ORG_A, NAME_A, 37, 8, now + 3 * HOUR, 0),
        [ORG_B]: acct(ORG_B, NAME_B, 91, 14, now - 2 * HOUR, 6),
        [ORG_D]: acct(ORG_D, NAME_D, 62, 26, now + HOUR, 1),
      },
      lastActiveOrg: ORG_A,
      widgetCollapsed: true,
    },
  });
  assert.ok(find(m.shadow, ".pill"), "the collapse preference was ignored");
  assert.equal(find(m.shadow, ".others"), null,
    "a new section re-expanded a widget the operator had shrunk");
});
