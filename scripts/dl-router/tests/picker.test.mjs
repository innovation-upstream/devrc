// Picker keyboard flow: filtering, the new-directory entry, arrows, Enter, Esc.
//
// The reducer is pure, so the whole flow is tested without a DOM. The Esc path
// matters most: it must resolve to the catch-all directory, i.e. leave the file
// exactly where the suggest() ladder already put it — no move, no alias.
import test from "node:test";
import assert from "node:assert/strict";

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

test("Enter on the top entry creates a new directory", () => {
  let state = initialState({ dirs: DIRS, suggestNew: "Aster Vale" });
  state = drive(state, [key("Enter")]);
  assert.deepEqual(state.done,
    { action: "choose", dir: "Aster Vale", createdNew: true });
});

test("typing then Enter creates the typed directory", () => {
  let state = initialState({ dirs: DIRS });
  state = drive(state, [input("aster nightingale"), key("Enter")]);
  assert.deepEqual(state.done,
    { action: "choose", dir: "Aster Nightingale", createdNew: true });
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
  assert.deepEqual(state.done,
    { action: "choose", dir: "Aster Nightingale", createdNew: true });
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

function mountPicker({ search = "?id=7&reason=r&suggestNew=Aster+Vale",
  snapshot = { dirs: DIRS.map((name) => ({ name })) }, deferReply = false } = {}) {
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
        return Promise.resolve({ ok: true });
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
  const choose = sent.find((m) => m.type === "dlr:choose");
  assert.deepEqual(choose, { type: "dlr:choose", downloadId: 7,
    dir: "Aster Vale", createdNew: true });
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
  state = drive(state, [key("Enter")]);
  assert.deepEqual(state.done,
    { action: "choose", dir: "Aster Vale", createdNew: true });
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
