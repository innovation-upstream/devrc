// Picker keyboard flow: filtering, the new-directory entry, arrows, Enter, Esc.
//
// The reducer is pure, so the whole flow is tested without a DOM. The Esc path
// matters most: it must resolve to the catch-all directory, i.e. leave the file
// exactly where the suggest() ladder already put it — no move, no alias.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

globalThis.DL_ROUTER_NO_AUTOSTART = true;

import {
  ENTRY_DIR, ENTRY_NEW, SNAPSHOT_ATTEMPTS, SNAPSHOT_RETRY_MS, filterEntries,
  initialState, mount, reduce, titleCase,
} from "../extension/picker.js";
import { makeDoc } from "./fake_page.mjs";

const DIRS = ["Jane Doe", "john-smith", "Mary_Major", "acme-studio", "other"];

const key = (k) => ({ type: "key", key: k });
const input = (v) => ({ type: "input", value: v });

function drive(state, events) {
  return events.reduce((s, e) => reduce(s, e), state);
}

// --- titleCase --------------------------------------------------------------- //
test("titleCase capitalises the first letter of each word only", () => {
  assert.equal(titleCase("aster nightingale"), "Aster Nightingale");
  assert.equal(titleCase("  spaced   out  "), "Spaced Out");
  assert.equal(titleCase("McBride"), "McBride");
  assert.equal(titleCase("o'neal vale"), "O'neal Vale");
  assert.equal(titleCase(""), "");
});

// --- filtering --------------------------------------------------------------- //
test("an empty query lists every directory", () => {
  const entries = filterEntries(DIRS, "", "");
  assert.deepEqual(entries.map((e) => e.name),
    DIRS.slice().sort((a, b) => a.localeCompare(b)));
});

test("typing a name that matches nothing puts the proposal on top", () => {
  const entries = filterEntries(DIRS, "aster nightingale", "");
  assert.equal(entries.length, 1);
  assert.equal(entries[0].kind, ENTRY_NEW);
});

test("the suggested new directory is the top entry when the query is empty", () => {
  const entries = filterEntries(DIRS, "", "Aster Vale");
  assert.equal(entries[0].kind, ENTRY_NEW);
  assert.equal(entries[0].name, "Aster Vale");
  assert.equal(entries[0].label, '+ new dir "Aster Vale"');
});


test("the new-directory entry moves to the bottom once the query matches", () => {
  // Otherwise typing "smith" and pressing Enter would CREATE "Smith" instead of
  // selecting the "john-smith" being filtered for.
  const entries = filterEntries(DIRS, "smith", "");
  assert.equal(entries[0].kind, ENTRY_DIR);
  assert.equal(entries[0].name, "john-smith");
  assert.equal(entries.at(-1).kind, ENTRY_NEW);
  assert.equal(entries.at(-1).name, "Smith");
});

test("typing replaces the proposal with a Title Case version of the query", () => {
  const entries = filterEntries(DIRS, "aster nightingale", "Aster Vale");
  const proposal = entries.find((e) => e.kind === ENTRY_NEW);
  assert.equal(proposal.name, "Aster Nightingale");
});

test("filtering folds naming conventions", () => {
  for (const q of ["jane", "JANE", "Jane Doe", "jane-doe", "jane_doe"]) {
    const names = filterEntries(DIRS, q, "").filter((e) => e.kind === ENTRY_DIR)
      .map((e) => e.name);
    assert.ok(names.includes("Jane Doe"), q);
  }
});

test("filtering matches a directory by a later token too", () => {
  const names = filterEntries(DIRS, "smith", "").map((e) => e.name);
  assert.ok(names.includes("john-smith"));
});

test("an exact match ranks above a prefix match", () => {
  const dirs = ["Vale", "Vale Extended", "Aster Vale"];
  const names = filterEntries(dirs, "vale", "").filter((e) => e.kind === ENTRY_DIR)
    .map((e) => e.name);
  assert.equal(names[0], "Vale");
});

test("no new-directory entry when the typed name already exists", () => {
  const entries = filterEntries(DIRS, "Jane Doe", "");
  assert.ok(entries.every((e) => e.kind !== ENTRY_NEW));
});

test("no new-directory entry for an unsafe typed name", () => {
  for (const q of ["../escape", "a/b", ".", ".."]) {
    const entries = filterEntries(DIRS, q, "");
    assert.ok(entries.every((e) => e.kind !== ENTRY_NEW), q);
  }
});

test("a query matching nothing still offers the new-directory entry", () => {
  const entries = filterEntries(DIRS, "nobody here", "");
  assert.equal(entries.length, 1);
  assert.equal(entries[0].kind, ENTRY_NEW);
});

// --- reducer ----------------------------------------------------------------- //
test("initialState builds the entry list up front", () => {
  const state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  assert.equal(state.index, 0);
  assert.equal(state.entries[0].kind, ENTRY_NEW);
  assert.equal(state.done, null);
});

test("arrows move the selection and clamp at both ends", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [key("ArrowUp")]);
  assert.equal(state.index, 0, "clamped at the top");
  state = drive(state, [key("ArrowDown"), key("ArrowDown")]);
  assert.equal(state.index, 2);
  state = drive(state, Array(50).fill(key("ArrowDown")));
  assert.equal(state.index, state.entries.length - 1, "clamped at the bottom");
});

test("Enter chooses the highlighted existing directory", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [input("smith"), key("Enter")]);
  assert.deepEqual(state.done,
    { action: "choose", dir: "john-smith", createdNew: false });
});

test("Enter on the top entry creates a new directory, after asking its kind", () => {
  // Creating a directory without saying which KIND it is leaves it
  // unclassified, and an unclassified directory never auto-files -- so it
  // would silently interrupt every future download into it. Hence the second
  // keypress.
  let state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  state = drive(state, [key("Enter")]);
  assert.equal(state.done, null, "nothing is created before the kind is known");
  assert.equal(state.pendingNew, "Aster Vale");
  assert.deepEqual(state.entries.map((e) => e.name), ["performer", "category"]);
  state = drive(state, [key("Enter")]);
  assert.deepEqual(state.done, { action: "choose", dir: "Aster Vale",
    createdNew: true, kind: "performer" });
});

test("the kind prompt can pick category", () => {
  let state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  state = drive(state, [key("Enter"), key("ArrowDown"), key("Enter")]);
  assert.equal(state.done.kind, "category");
});

test("Escape in the kind prompt goes back to the list, not out of the picker", () => {
  let state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  state = drive(state, [key("Enter"), key("Escape")]);
  assert.equal(state.done, null, "escaping a sub-question undoes the question");
  assert.equal(state.pendingNew, null);
  assert.equal(state.entries[0].name, "Aster Vale");
});

test("typing while the kind prompt is up cannot rebuild the list under it", () => {
  let state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  state = drive(state, [key("Enter"), input("jane")]);
  assert.equal(state.pendingNew, "Aster Vale");
  assert.deepEqual(state.entries.map((e) => e.name), ["performer", "category"]);
});

test("typing then Enter creates the typed directory", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [input("aster nightingale"), key("Enter"),
    key("Enter")]);
  assert.deepEqual(state.done, { action: "choose", dir: "Aster Nightingale",
    createdNew: true, kind: "performer" });
});

test("typing resets the highlight to the top", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [key("ArrowDown"), key("ArrowDown"), input("j")]);
  assert.equal(state.index, 0);
});

test("Escape resolves to the catch-all directory and chooses nothing", () => {
  let state = initialState({ dirs: DIRS, otherDir: "other" });
  state = drive(state, [input("jane"), key("Escape")]);
  assert.deepEqual(state.done, { action: "cancel", dir: "other" });
});

test("Escape honours a custom catch-all name", () => {
  let state = initialState({ dirs: DIRS, otherDir: "catch-all" });
  state = drive(state, [key("Escape")]);
  assert.equal(state.done.dir, "catch-all");
});

test("Enter with no entries at all does nothing", () => {
  let state = initialState({ dirs: [] });
  state = drive(state, [key("Enter")]);
  assert.equal(state.done, null);
});

test("events after the flow finished are ignored", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [key("Escape")]);
  const after = drive(state, [input("jane"), key("Enter"), key("ArrowDown")]);
  assert.deepEqual(after.done, { action: "cancel", dir: "other" });
  assert.equal(after.query, "");
});

test("unknown keys and malformed events are ignored", () => {
  const state = initialState({ dirs: DIRS });
  for (const e of [key("Tab"), key("a"), null, undefined, { type: "nope" }]) {
    assert.equal(reduce(state, e), state);
  }
});

test("the reducer never mutates the previous state", () => {
  const state = initialState({ dirs: DIRS });
  const before = JSON.stringify(state);
  reduce(state, input("jane"));
  reduce(state, key("ArrowDown"));
  assert.equal(JSON.stringify(state), before);
});

// --- the load-order race ----------------------------------------------------- //
//
// mount() starts with `dirs: []`, fills it from an async `dlr:snapshot`
// round-trip, and the window opens FOCUSED. Typing a query and pressing Enter
// before the answer landed CREATED a new directory -- with nothing loaded,
// filterEntries has nothing to match, so the "+ new dir" proposal is the only
// entry and it is on top. That re-opened the exact footgun the entry-ordering
// rule was written to close.
test("Enter is deferred, not acted on, while the directory list is loading", () => {
  let state = initialState({ dirs: [], suggestNew: "", loading: true });
  state = drive(state, [input("smith"), key("Enter")]);
  assert.equal(state.done, null, "Enter must not create a directory yet");
  assert.equal(state.pendingEnter, true);
});

test("the deferred Enter selects the existing directory once it arrives", () => {
  let state = initialState({ dirs: [], loading: true });
  state = drive(state, [input("smith"), key("Enter")]);
  state = reduce(state, { type: "dirs", dirs: DIRS });
  assert.deepEqual(state.done,
    { action: "choose", dir: "john-smith", createdNew: false },
    "the race used to create a directory called 'Smith' here");
});

test("a deferred Enter still creates when nothing really matches", () => {
  let state = initialState({ dirs: [], loading: true });
  state = drive(state, [input("aster nightingale"), key("Enter")]);
  state = reduce(state, { type: "dirs", dirs: DIRS });
  state = drive(state, [key("Enter")]);          // the kind prompt
  assert.deepEqual(state.done, { action: "choose", dir: "Aster Nightingale",
    createdNew: true, kind: "performer" });
});

test("Escape works immediately even while loading", () => {
  let state = initialState({ dirs: [], loading: true, otherDir: "other" });
  state = drive(state, [key("Escape")]);
  assert.deepEqual(state.done, { action: "cancel", dir: "other" });
});

test("arrows while loading do not queue a choice", () => {
  let state = initialState({ dirs: [], loading: true });
  state = drive(state, [key("ArrowDown"), key("ArrowDown")]);
  assert.equal(state.done, null);
  assert.equal(state.pendingEnter, false);
});

test("the dirs event resets the highlight and clears loading", () => {
  let state = initialState({ dirs: [], loading: true });
  state = reduce(state, { type: "dirs", dirs: DIRS });
  assert.equal(state.loading, false);
  assert.equal(state.index, 0);
  assert.deepEqual(state.dirs, DIRS);
});

test("a malformed dirs event leaves an empty but usable list", () => {
  let state = initialState({ dirs: [], loading: true });
  state = reduce(state, { type: "dirs", dirs: "nope" });
  assert.deepEqual(state.dirs, []);
  assert.equal(state.loading, false);
});

// --- mount() ----------------------------------------------------------------- //
const PAGE_IDS = ["q", "list", "meta", "dup"];

// A sentinel, because `chooseResult: undefined` would hit the default
// parameter and so could never express "the worker answered with nothing".
const DEFAULT_CHOOSE = Symbol("default-choose");

function mountPicker({ search = "?id=7&reason=r&suggestNew=Aster+Vale",
  snapshot = { dirs: DIRS.map((name) => ({ name })) }, deferReply = false,
  chooseResult = DEFAULT_CHOOSE } = {}) {
  const doc = makeDoc(PAGE_IDS, { search });
  const sent = [];
  let replySnapshot = null;
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          replySnapshot = () => cb({ ok: true, snapshot });
          if (!deferReply) replySnapshot();
        }
        if (msg.type !== "dlr:choose") return Promise.resolve({ ok: true });
        return Promise.resolve(
          chooseResult === DEFAULT_CHOOSE ? { ok: true } : chooseResult);
      },
    },
  };
  let closed = 0;
  const handle = mount(doc, chromeApi, { closeWindow: () => { closed += 1; } });
  return { doc, sent, handle, land: () => replySnapshot && replySnapshot(),
    closed: () => closed };
}

test("mount renders the reason, focuses the input and asks for the snapshot", () => {
  const { doc, sent } = mountPicker();
  assert.equal(doc.getElementById("meta").textContent, "r");
  assert.equal(doc.getElementById("q").focused, true);
  assert.ok(sent.some((m) => m.type === "dlr:snapshot"));
});

test("mount shows a loading row until the directories arrive", () => {
  const { doc, land } = mountPicker({ deferReply: true });
  assert.match(doc.getElementById("list").textContent, /Loading directories/);
  land();
  assert.ok(!doc.getElementById("list").textContent.includes("Loading"));
  assert.match(doc.getElementById("list").textContent, /john-smith/);
});

const tick = () => new Promise((r) => setTimeout(r, 0));

test("mount: typing + Enter BEFORE the snapshot lands selects, never creates", async () => {
  // The end-to-end version of the race, driven through the real DOM wiring.
  const { doc, sent, land, closed } = mountPicker({ deferReply: true });
  doc.getElementById("q").value = "smith";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 0,
    "nothing may be chosen while the list is still empty");
  land();
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.deepEqual(choose,
    { type: "dlr:choose", downloadId: 7, dir: "john-smith", createdNew: false });
  await tick();
  assert.equal(closed(), 1);
});

test("mount: the normal flow still selects and closes", async () => {
  const { doc, sent, closed } = mountPicker();
  doc.getElementById("q").value = "jane";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.equal(choose.dir, "Jane Doe");
  await tick();
  assert.equal(closed(), 1);
});

test("mount: Escape closes without choosing anything", async () => {
  const { doc, sent, closed } = mountPicker();
  doc.fire("keydown", { key: "Escape" });
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 0);
  await tick();
  assert.equal(closed(), 1);
});

test("mount: keys the picker does not own are left alone", () => {
  const { doc } = mountPicker();
  const e = doc.fire("keydown", { key: "a" });
  assert.notEqual(e.defaultPrevented, true);
});

test("mount: arrows move the highlight in the rendered list", () => {
  const { doc } = mountPicker();
  doc.fire("keydown", { key: "ArrowDown" });
  const rows = doc.getElementById("list").children;
  assert.ok(rows[1].className.includes("sel"));
});

test("mount: a yt-dlp job id survives as a string", () => {
  const { doc, sent } = mountPicker({ search: "?id=fetch%3A3" });
  doc.fire("keydown", { key: "Enter" });
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.equal(choose.downloadId, "fetch:3");
});

test("mount: the duplicate warning is shown when there is one", () => {
  const { doc } = mountPicker({ search: "?id=7&dup=Possible+duplicate" });
  assert.equal(doc.getElementById("dup").textContent, "Possible duplicate");
});

test("mount: an empty snapshot still offers the new-directory entry", () => {
  const { doc, sent } = mountPicker({ snapshot: { dirs: [] },
    search: "?id=7&suggestNew=Aster+Vale" });
  assert.match(doc.getElementById("list").textContent, /new dir "Aster Vale"/);
  doc.fire("keydown", { key: "Enter" });
  doc.fire("keydown", { key: "Enter" });        // performer
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.deepEqual(choose, { type: "dlr:choose", downloadId: 7,
    dir: "Aster Vale", createdNew: true, kind: "performer" });
});


// --- a FAILED snapshot is not an empty library ------------------------------ //
//
// The picker is a separate popup the user can leave open past MV3's ~30 s idle
// teardown. On a cold-woken worker the /dirs fetch 401s, and treating that as
// "there are no directories" makes the new-directory proposal the only entry --
// so typing a name and pressing Enter CREATES one instead of selecting the
// existing match. Finding 16, back through a different door.
test("a failed snapshot does not clear loading with an empty list", () => {
  let state = initialState({ dirs: [], loading: true });
  state = drive(state, [input("smith"), key("Enter")]);
  assert.equal(state.pendingEnter, true);
  state = reduce(state, { type: "dirs-failed" });
  assert.equal(state.failed, true);
  assert.equal(state.done, null, "a pending Enter must not become a creation");
  assert.equal(state.pendingEnter, false);
});

test("Enter does nothing at all once the list is known to be unavailable", () => {
  let state = initialState({ dirs: [], loading: true });
  state = reduce(state, { type: "dirs-failed" });
  state = drive(state, [input("aster nightingale"), key("Enter")]);
  assert.equal(state.done, null, "'no answer' is not 'no directories exist'");
});

test("Escape still works when the list could not be loaded", () => {
  let state = initialState({ dirs: [], loading: true, otherDir: "other" });
  state = reduce(state, { type: "dirs-failed" });
  state = drive(state, [key("Escape")]);
  assert.deepEqual(state.done, { action: "cancel", dir: "other" });
});

test("a GENUINELY empty library does clear loading", () => {
  // ok-with-no-dirs is a real answer; creating is then the only sane action.
  let state = initialState({ dirs: [], loading: true, suggestNew: "Aster Vale" });
  state = reduce(state, { type: "dirs", dirs: [] });
  assert.equal(state.loading, false);
  assert.equal(state.failed, false);
  state = drive(state, [key("Enter"), key("Enter")]);
  assert.deepEqual(state.done, { action: "choose", dir: "Aster Vale",
    createdNew: true, kind: "performer" });
});

test("mount retries a failed snapshot rather than giving up on the first no", async () => {
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const sent = [];
  let answers = 0;
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          answers += 1;
          // The first two answers are what a cold worker gives.
          if (answers <= 2) cb({ ok: false, snapshot: null });
          else cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
        }
        return Promise.resolve({ ok: true });
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => {} });
  assert.match(doc.getElementById("list").textContent, /Loading directories/);
  await new Promise((r) => setTimeout(r, SNAPSHOT_RETRY_MS * 3 + 60));
  assert.ok(answers >= 3, `only asked ${answers} times`);
  assert.match(doc.getElementById("list").textContent, /john-smith/);
});

test("mount: typing + Enter while the worker is cold selects once it wakes", async () => {
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const sent = [];
  let answers = 0;
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          answers += 1;
          if (answers === 1) cb({ ok: false, snapshot: null });
          else cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
        }
        return Promise.resolve({ ok: true });
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => {} });
  doc.getElementById("q").value = "smith";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 0);
  await new Promise((r) => setTimeout(r, SNAPSHOT_RETRY_MS * 2 + 60));
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.deepEqual(choose,
    { type: "dlr:choose", downloadId: 7, dir: "john-smith", createdNew: false },
    "a cold worker must not turn the user's selection into a creation");
});

test("mount gives up eventually and says so, without creating anything", async () => {
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const sent = [];
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") cb({ ok: false, snapshot: null });
        return Promise.resolve({ ok: true });
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => {} });
  doc.getElementById("q").value = "smith";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  await new Promise((r) => setTimeout(r, SNAPSHOT_RETRY_MS * (SNAPSHOT_ATTEMPTS + 2)));
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 0);
  assert.match(doc.getElementById("list").textContent, /Could not reach/);
});


// --- a refused choice must be VISIBLE ---------------------------------------- //
//
// finish() awaited sendMessage, DISCARDED the response and closed the window
// regardless -- and EVERY choose goes through the picker, so an immediate
// refusal (the sidecar could not prove the router created the file) was never
// surfaced to anyone.
test("a refused choice keeps the window open and says why", async () => {
  const { doc, sent, closed } = mountPicker({
    chooseResult: { ok: false, error: "cannot prove it created this file" },
  });
  doc.getElementById("q").value = "jane";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  await tick();
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 1);
  assert.equal(closed(), 0, "closing on a refusal is how it stayed invisible");
  assert.match(doc.getElementById("meta").textContent, /Could not file into/);
  assert.match(doc.getElementById("meta").textContent, /cannot prove/);
});

test("after a refusal the user can still pick somewhere else", async () => {
  let result = { ok: false, error: "refused" };
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const sent = [];
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
        }
        return Promise.resolve(
          msg.type === "dlr:choose" ? result : { ok: true });
      },
    },
  };
  let closed = 0;
  mount(doc, chromeApi, { closeWindow: () => { closed += 1; } });

  doc.getElementById("q").value = "jane";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  await tick();
  assert.equal(closed, 0);

  // The window is still usable: a second, accepted pick goes through.
  result = { ok: true };
  doc.getElementById("q").value = "smith";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  await tick();
  const chooses = sent.filter((m) => m.type === "dlr:choose");
  assert.equal(chooses.length, 2);
  assert.equal(chooses[1].dir, "john-smith");
  assert.equal(closed, 1);
});

test("Escape still works after a refusal", async () => {
  const { doc, sent, closed } = mountPicker({
    chooseResult: { ok: false, error: "refused" },
  });
  doc.fire("keydown", { key: "Enter" });        // the new-directory entry
  doc.fire("keydown", { key: "Enter" });        // its kind
  await tick();
  doc.fire("keydown", { key: "Escape" });
  await tick();
  assert.equal(closed(), 1);
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 1);
});

test("an accepted choice still closes the window", async () => {
  const { doc, closed } = mountPicker({ chooseResult: { ok: true, dir: "x" } });
  doc.fire("keydown", { key: "Enter" });
  doc.fire("keydown", { key: "Enter" });        // its kind
  await tick();
  assert.equal(closed(), 1);
});

test("Enter retries after the directory list could not be loaded", async () => {
  // `retry` was exported but nothing wired it, so after ~2.4s the window was
  // Esc-only.
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  let answers = 0;
  const sent = [];
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          answers += 1;
          if (answers <= SNAPSHOT_ATTEMPTS) cb({ ok: false, snapshot: null });
          else cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
        }
        return Promise.resolve({ ok: true });
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => {} });
  await new Promise((r) => setTimeout(r, SNAPSHOT_RETRY_MS * (SNAPSHOT_ATTEMPTS + 2)));
  assert.match(doc.getElementById("list").textContent, /Enter to retry/);

  doc.fire("keydown", { key: "Enter" });
  await new Promise((r) => setTimeout(r, SNAPSHOT_RETRY_MS + 60));
  assert.match(doc.getElementById("list").textContent, /john-smith/);
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 0,
    "the retry must not double as a selection");
});


// --- the loop fix, pinned DETERMINISTICALLY --------------------------------- //
//
// `choose-failed` appeared zero times in this file: the ordering was only
// exercised through mount(), so a regression surfaced as a whole-file OOM
// rather than an assertion. A green suite hid this once already.
test("choose-failed clears `done` even though done is already set", () => {
  const done = { ...initialState({ dirs: DIRS }),
    done: { action: "choose", dir: "Jane Doe", createdNew: false } };
  const next = reduce(done, { type: "choose-failed", message: "refused" });
  assert.equal(next.done, null,
    "reduce's `state.done` early-return must not swallow this event");
  assert.match(next.error, /refused/);
});

test("choose-failed is the ONLY event that survives the done guard", () => {
  // Anything else must still be inert once the flow has finished.
  const done = { ...initialState({ dirs: DIRS }),
    done: { action: "choose", dir: "Jane Doe", createdNew: false } };
  for (const event of [input("smith"), key("Enter"), key("Escape"),
    key("ArrowDown"), { type: "dirs", dirs: DIRS }, { type: "dirs-failed" }]) {
    assert.equal(reduce(done, event), done, JSON.stringify(event));
  }
});

test("choose-failed supplies a default message", () => {
  const done = { ...initialState({ dirs: DIRS }),
    done: { action: "choose", dir: "x", createdNew: false } };
  assert.ok(reduce(done, { type: "choose-failed" }).error.length > 0);
});

// --- one choose at a time ---------------------------------------------------- //
test("keypresses during an in-flight choose do not re-send it", async () => {
  // `reduce` returns unchanged state once `done` is set, but apply() ran
  // `if (state.done) void finish()` regardless -- so every later keypress
  // fired another dlr:choose. Measured before the fix: 3 keypresses -> 4
  // messages.
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const sent = [];
  let release;
  const gate = new Promise((r) => { release = r; });
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
          return Promise.resolve({ ok: true });
        }
        return gate.then(() => ({ ok: true }));
      },
    },
  };
  let closed = 0;
  mount(doc, chromeApi, { closeWindow: () => { closed += 1; } });

  doc.fire("keydown", { key: "Enter" });          // starts the choose
  doc.fire("keydown", { key: "ArrowDown" });
  doc.fire("keydown", { key: "Enter" });
  doc.fire("keydown", { key: "Escape" });
  await tick();
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 1,
    "exactly one choose may be in flight");
  release();
  await tick();
  assert.equal(closed, 1);
});

// --- an unknown outcome is not a success ------------------------------------- //
test("a missing response is treated as a refusal, not a silent close", async () => {
  // Chrome's sendMessage behaviour when the worker is torn down mid-choose is
  // not something this code can verify, so an absent answer must not close the
  // window on an unknown outcome. Built inline rather than through
  // mountPicker: `chooseResult: undefined` would hit the default parameter and
  // so could never express "resolved with nothing".
  for (const answer of [undefined, null]) {
    const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
    const chromeApi = {
      runtime: {
        sendMessage: (msg, cb) => {
          if (typeof cb === "function") {
            cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
            return Promise.resolve({ ok: true });
          }
          return Promise.resolve(answer);
        },
      },
    };
    let closed = 0;
    mount(doc, chromeApi, { closeWindow: () => { closed += 1; } });
    doc.fire("keydown", { key: "Enter" });
    await tick();
    assert.equal(closed, 0, `closed on ${String(answer)}`);
    assert.match(doc.getElementById("meta").textContent, /no answer/);
  }
});

// --- a stale refusal must not sit through the next attempt ------------------- //
test("typing clears a previous refusal", () => {
  let state = initialState({ dirs: DIRS });
  state = reduce({ ...state, done: { action: "choose", dir: "x" } },
    { type: "choose-failed", message: "refused" });
  assert.ok(state.error);
  state = reduce(state, input("jane"));
  assert.equal(state.error, "", "a new attempt is not about the old failure");
});

test("a new Enter clears the previous refusal", () => {
  let state = initialState({ dirs: DIRS });
  state = reduce({ ...state, done: { action: "choose", dir: "x" } },
    { type: "choose-failed", message: "refused" });
  state = reduce(state, key("Enter"));
  assert.equal(state.error, "");
  assert.ok(state.done);
});

test("the in-flight choose shows progress rather than the stale error", async () => {
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  let release;
  const gate = new Promise((r) => { release = r; });
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
          return Promise.resolve({ ok: true });
        }
        return gate.then(() => ({ ok: true }));
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => {} });
  doc.getElementById("q").value = "jane";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  await tick();
  assert.match(doc.getElementById("meta").textContent, /Filing into "Jane Doe"/);
  release();
  await tick();
});


// --- the single-flight guard must LATCH, not just cover the flight ---------- //
//
// An in-flight flag alone reset in .finally() while `done` stayed set on the
// success and cancel paths, so once the choose SETTLED every later event
// re-entered finish() and re-sent. Measured on that version: 1 Enter + 3 keys
// -> 4 messages; 1 Enter + 1 keystroke in the filter box -> 2. It was masked
// on the happy path only by close() tearing the page down, and whether
// window.close() on a popup is instant has never been observed in a browser.
function latchHarness() {
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const sent = [];
  let closed = 0;
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
          return Promise.resolve({ ok: true });
        }
        return Promise.resolve({ ok: true });
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => { closed += 1; } });
  return { doc, sent, closed: () => closed };
}

test("events AFTER a settled choose do not re-send it", async () => {
  const { doc, sent } = latchHarness();
  doc.fire("keydown", { key: "Enter" });
  await tick();                      // the choose settles and close() runs
  doc.fire("keydown", { key: "ArrowDown" });
  doc.fire("keydown", { key: "Enter" });
  doc.fire("keydown", { key: "Escape" });
  await tick();
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 1,
    "the guard must stay latched once `done` is set");
});

test("a single keystroke after a settled choose does not re-send it", async () => {
  const { doc, sent } = latchHarness();
  doc.fire("keydown", { key: "Enter" });
  await tick();
  doc.getElementById("q").value = "j";
  doc.getElementById("q").fire("input");
  await tick();
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 1);
});

test("a settled CANCEL is latched too", async () => {
  const { doc, sent, closed } = latchHarness();
  doc.fire("keydown", { key: "Escape" });
  await tick();
  doc.fire("keydown", { key: "Enter" });
  doc.fire("keydown", { key: "Escape" });
  await tick();
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 0);
  assert.equal(closed(), 1, "close() must not be called again either");
});

test("but a REFUSED choose unlatches, so retry still works", async () => {
  // The refusal path clears `done`, which is exactly what unlatches the guard.
  let answer = { ok: false, error: "refused" };
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const sent = [];
  let closed = 0;
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
          return Promise.resolve({ ok: true });
        }
        return Promise.resolve(answer);
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => { closed += 1; } });
  doc.fire("keydown", { key: "Enter" });
  await tick();
  answer = { ok: true };
  doc.fire("keydown", { key: "Enter" });
  await tick();
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 2);
  assert.equal(closed, 1);
});

// --- the in-flight message must survive a render ---------------------------- //
test("an event during an in-flight choose does not blank the meta line", async () => {
  // `Filing into "X"...` was written straight to meta.textContent, so the next
  // render() erased it -- and any event triggers one. Probed: after Escape
  // during a never-settling choose, meta was "" on a picker that is (correctly)
  // unresponsive until the choose settles.
  const doc = makeDoc(PAGE_IDS, { search: "?id=7&reason=tag" });
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
          return Promise.resolve({ ok: true });
        }
        return new Promise(() => {});      // never settles
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => {} });
  doc.getElementById("q").value = "jane";
  doc.getElementById("q").fire("input");
  doc.fire("keydown", { key: "Enter" });
  await tick();
  assert.match(doc.getElementById("meta").textContent, /Filing into "Jane Doe"/);

  for (const key of ["Escape", "ArrowDown", "Enter"]) {
    doc.fire("keydown", { key });
    await tick();
    assert.match(doc.getElementById("meta").textContent,
      /Filing into "Jane Doe"/, `blanked by ${key}`);
  }
});

test("the status is carried in state, not painted on the DOM", () => {
  const state = initialState({ dirs: DIRS });
  const next = reduce({ ...state, done: { action: "choose", dir: "Jane Doe" } },
    { type: "choosing", dir: "Jane Doe" });
  assert.match(next.status, /Filing into "Jane Doe"/);
  // ...and it survives the done guard, like choose-failed and for the same
  // reason: it is dispatched WHILE done is set.
  assert.notEqual(next.status, "");
});

test("a refusal replaces the in-flight status rather than stacking on it", () => {
  let state = initialState({ dirs: DIRS });
  state = reduce({ ...state, done: { action: "choose", dir: "x" } },
    { type: "choosing", dir: "x" });
  state = reduce(state, { type: "choose-failed", message: "refused" });
  assert.equal(state.status, "");
  assert.match(state.error, /refused/);
});

test("typing clears the in-flight status too", () => {
  let state = initialState({ dirs: DIRS });
  state = reduce({ ...state, done: { action: "choose", dir: "x" } },
    { type: "choosing", dir: "x" });
  state = reduce({ ...state, done: null }, input("jane"));
  assert.equal(state.status, "");
});

test("a caught sendMessage rejection is not Error-prefixed", async () => {
  const doc = makeDoc(PAGE_IDS, { search: "?id=7" });
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
          return Promise.resolve({ ok: true });
        }
        return Promise.reject(new Error("the message port closed"));
      },
    },
  };
  mount(doc, chromeApi, { closeWindow: () => {} });
  doc.fire("keydown", { key: "Enter" });
  await tick();
  const shown = doc.getElementById("meta").textContent;
  assert.match(shown, /the message port closed/);
  assert.ok(!shown.includes("Error:"), shown);
});

// --- per-directory item counts ----------------------------------------------- //
//
// The number rides as its own field and its own DOM node. It is decorative:
// nothing routes on it, and it must never end up inside `label`, which is what
// the new-directory proposal and the sidecar-facing flow are built from.
const COUNTS = { "Jane Doe": 12, "john-smith": 3, "Mary_Major": 0 };

test("filterEntries attaches each directory's count", () => {
  const byName = Object.fromEntries(
    filterEntries(DIRS, "", "", COUNTS).map((e) => [e.name, e.count]));
  assert.equal(byName["Jane Doe"], 12);
  assert.equal(byName["john-smith"], 3);
  assert.equal(byName["Mary_Major"], 0, "zero is a count, not a missing one");
  assert.equal(byName["acme-studio"], undefined, "absent stays absent");
});

test("no counts map at all leaves every entry countless", () => {
  for (const entry of filterEntries(DIRS, "", "")) {
    assert.equal(entry.count, undefined);
  }
});

test("a malformed count is dropped rather than rendered", () => {
  const hostile = { "Jane Doe": "12", "john-smith": NaN, "Mary_Major": -1,
    "acme-studio": Infinity, other: null };
  for (const entry of filterEntries(DIRS, "", "", hostile)) {
    assert.equal(entry.count, undefined, entry.name);
  }
});

test("the count NEVER leaks into the entry label", () => {
  // `label` is what the "+ new dir" proposal is built from and what the flow
  // reads; a decorative number in it would be a directory name with a number
  // stuck on the end.
  for (const entry of filterEntries(DIRS, "", "", COUNTS)) {
    assert.equal(entry.label, entry.name);
  }
});

test("the new-directory proposal carries no count", () => {
  const entries = filterEntries(DIRS, "aster nightingale", "", COUNTS);
  const proposal = entries.find((e) => e.kind === ENTRY_NEW);
  assert.equal(proposal.count, undefined);
});

test("the dirs event carries the counts into the entries", () => {
  let state = initialState({ dirs: [], loading: true });
  state = reduce(state, { type: "dirs", dirs: DIRS, counts: COUNTS });
  const jane = state.entries.find((e) => e.name === "Jane Doe");
  assert.equal(jane.count, 12);
  // ...and they survive a re-filter, which rebuilds the list from scratch.
  state = reduce(state, input("jane"));
  assert.equal(state.entries[0].count, 12);
});

test("a snapshot with no counts renders exactly as it always did", () => {
  let state = initialState({ dirs: [], loading: true });
  state = reduce(state, { type: "dirs", dirs: DIRS });
  assert.equal(state.counts, null);
  assert.ok(state.entries.every((e) => e.count === undefined));
});

test("mount renders the count beside the directory name", () => {
  const { doc } = mountPicker({
    snapshot: { dirs: DIRS.map((name) => ({ name })), counts: COUNTS } });
  const rows = doc.getElementById("list").children;
  const jane = rows.find((r) => r.textContent.includes("Jane Doe"));
  const badge = jane.children.find((c) => c.className === "count");
  assert.equal(badge.textContent, "12");
  // The name is its own node, so the count can be styled down and can never be
  // mistaken for part of it.
  assert.equal(jane.children[0].textContent, "Jane Doe");
});

test("mount draws no count node when the sidecar sent none", () => {
  const { doc } = mountPicker();
  const rows = doc.getElementById("list").children;
  for (const row of rows) {
    assert.ok(!row.children.some((c) => c.className === "count"));
  }
});

// --- click to select --------------------------------------------------------- //
const click = (index) => ({ type: "click", index });

test("a click chooses the clicked row, not the highlighted one", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [key("ArrowDown")]);           // highlight moves to 1
  const target = state.entries[3].name;
  state = drive(state, [click(3)]);
  assert.deepEqual(state.done,
    { action: "choose", dir: target, createdNew: false });
  assert.equal(state.index, 3, "the highlight follows the click");
});

test("a click on the new-directory entry asks the kind first", () => {
  // Creation by mouse must be exactly as deliberate as creation by keyboard.
  let state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  state = drive(state, [click(0)]);
  assert.equal(state.done, null, "nothing is created before the kind is known");
  assert.equal(state.pendingNew, "Aster Vale");
  assert.deepEqual(state.entries.map((e) => e.name), ["performer", "category"]);
});

test("a click answers the kind prompt", () => {
  let state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  state = drive(state, [click(0), click(1)]);
  assert.deepEqual(state.done, { action: "choose", dir: "Aster Vale",
    createdNew: true, kind: "category" });
});

test("a click while the directory list is still loading is DROPPED", () => {
  // THE pin. Enter DEFERS while loading because a typed query survives the
  // list arriving. A click's intent is a screen position, and position N of the
  // loading placeholder is not position N of the real list -- honouring it
  // later would choose an arbitrary directory, or CREATE one, which is the
  // exact footgun the deferred Enter exists to close.
  const state = initialState({ dirs: [], suggestNew: "Aster Vale",
    loading: true });
  assert.equal(state.entries[0].kind, ENTRY_NEW,
    "the proposal really is sitting at index 0 while loading");
  const after = reduce(state, click(0));
  assert.equal(after, state, "state is returned untouched");
  assert.equal(after.done, null);
  assert.equal(after.pendingNew, null);
  assert.equal(after.pendingEnter, false, "and it is not queued for later");
});

test("a click is refused once the list is known to be unavailable", () => {
  let state = initialState({ dirs: [], suggestNew: "Aster Vale", loading: true });
  state = reduce(state, { type: "dirs-failed" });
  const after = reduce(state, click(0));
  assert.equal(after, state);
  assert.equal(after.done, null);
});

test("a click with a nonsense index does nothing", () => {
  const state = initialState({ dirs: DIRS });
  for (const i of [-1, 99, 1.5, NaN, "2", null, undefined]) {
    assert.equal(reduce(state, click(i)), state, String(i));
  }
});

test("a click after the flow finished is ignored", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [key("Escape")]);
  const after = reduce(state, click(1));
  assert.equal(after, state);
  assert.deepEqual(after.done, { action: "cancel", dir: "other" });
});

test("the reducer never mutates the previous state on a click", () => {
  const before = initialState({ dirs: DIRS });
  const snapshot = JSON.stringify(before);
  reduce(before, click(2));
  assert.equal(JSON.stringify(before), snapshot);
});

test("mount: clicking a rendered row chooses that directory", async () => {
  const { doc, sent, closed } = mountPicker();
  const rows = doc.getElementById("list").children;
  const wanted = rows[3].children[0].textContent;
  rows[3].fire("click");
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.deepEqual(choose, { type: "dlr:choose", downloadId: 7, dir: wanted,
    createdNew: false });
  await tick();
  assert.equal(closed(), 1);
});

test("mount: the loading placeholder is not clickable at all", () => {
  // Belt and braces around the reducer's refusal: the row that stands in for
  // the list while it loads never even gets a handler.
  const { doc } = mountPicker({ deferReply: true });
  const rows = doc.getElementById("list").children;
  assert.match(rows[0].textContent, /Loading directories/);
  assert.equal(rows[0].listeners.has("click"), false);
});

test("mount: the keyboard still drives the list after a click", async () => {
  // The keyboard-first flow must not become a second-class citizen. Clicking
  // the new-directory row raises the kind prompt; arrows and Enter still answer
  // it, without touching the mouse again.
  const { doc, sent } = mountPicker();
  doc.getElementById("list").children[0].fire("click");   // "+ new dir"
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 0);
  doc.fire("keydown", { key: "ArrowDown" });
  doc.fire("keydown", { key: "Enter" });
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.deepEqual(choose, { type: "dlr:choose", downloadId: 7,
    dir: "Aster Vale", createdNew: true, kind: "category" });
});

test("mount: clicks during an in-flight choose do not re-send it", async () => {
  // The same single-flight latch the keyboard is held to. A click is a much
  // easier way to produce three of them in a row.
  const { doc, sent } = mountPicker({
    chooseResult: new Promise(() => {}) });
  const rows = () => doc.getElementById("list").children;
  rows()[2].fire("click");
  await tick();
  rows()[3].fire("click");
  rows()[4].fire("click");
  await tick();
  assert.equal(sent.filter((m) => m.type === "dlr:choose").length, 1);
});

test("mount: the chosen row is marked for the confirmation animation", () => {
  const { doc } = mountPicker({ deferReply: false });
  const rows = doc.getElementById("list").children;
  rows[2].fire("click");
  // Re-read: render() rebuilds the list.
  assert.ok(doc.getElementById("list").children[2].className.includes("taken"),
    "the row the user picked is the one that pulses");
  assert.ok(!doc.getElementById("list").children[1].className.includes("taken"));
});

test("mount: a keyboard choice marks the same way a click does", () => {
  const { doc } = mountPicker();
  doc.fire("keydown", { key: "ArrowDown" });
  doc.fire("keydown", { key: "Enter" });
  assert.ok(doc.getElementById("list").children[1].className.includes("taken"));
});

test("the picker's motion is opt-out", () => {
  // The animation is CSS, so this is the only place it can be pinned headlessly
  // -- but an unconditional animation is a real accessibility regression and
  // deleting the media query must not be silent.
  const css = readFileSync(
    new URL("../extension/picker.html", import.meta.url), "utf8");
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /\.row\.taken/);
  // Hover is CSS-only on purpose: a pointer that moved the selection would
  // fight the keyboard, because the mouse sits wherever it was left.
  assert.match(css, /\.row:hover/);
});

// --- delivered as an in-page overlay ----------------------------------------- //
//
// Same document, same reducer, same finish() -- only "what does closing mean"
// differs, because an iframe cannot close itself.
function mountFree(search) {
  const doc = makeDoc(PAGE_IDS, { search });
  const sent = [];
  const chromeApi = {
    runtime: {
      sendMessage: (msg, cb) => {
        sent.push(msg);
        if (typeof cb === "function") {
          cb({ ok: true, snapshot: { dirs: DIRS.map((name) => ({ name })) } });
        }
        return Promise.resolve({ ok: true });
      },
    },
  };
  return { doc, sent, handle: mount(doc, chromeApi) };
}

test("an embedded picker announces that it actually booted", () => {
  // The service worker will not consider the overlay delivered without this:
  // a content-script-injected iframe is subject to the PAGE's CSP, so the host
  // element can exist while the frame never loaded. Only an extension context
  // that really started can send it.
  const { sent } = mountFree("?id=7&embed=1&overlay=ov-abc");
  assert.deepEqual(sent.find((m) => m.type === "dlr:picker-ready"),
    { type: "dlr:picker-ready", overlay: "ov-abc" });
});

test("a windowed picker announces nothing", () => {
  const { sent } = mountFree("?id=7");
  assert.equal(sent.some((m) => m.type === "dlr:picker-ready"), false);
});

test("an embedded picker asks the worker to tear the overlay down", async () => {
  // window.close() on an iframe is a no-op, so without this the overlay would
  // outlive the pick and sit on the page forever.
  const { doc, sent } = mountFree("?id=7&embed=1&overlay=ov-abc");
  doc.fire("keydown", { key: "Escape" });
  await tick();
  assert.deepEqual(sent.find((m) => m.type === "dlr:picker-closed"),
    { type: "dlr:picker-closed", overlay: "ov-abc" });
});

test("an accepted choice in an overlay closes it too", async () => {
  const { doc, sent } = mountFree("?id=7&embed=1&overlay=ov-abc");
  doc.getElementById("list").children[0].fire("click");
  await tick();
  assert.ok(sent.some((m) => m.type === "dlr:choose"));
  assert.ok(sent.some((m) => m.type === "dlr:picker-closed"));
});
