// Picker keyboard flow: filtering, the new-directory entry, arrows, Enter, Esc.
//
// The reducer is pure, so the whole flow is tested without a DOM. The Esc path
// matters most: it must resolve to the catch-all directory, i.e. leave the file
// exactly where the suggest() ladder already put it — no move, no alias.
import test from "node:test";
import assert from "node:assert/strict";

import {
  ENTRY_DIR, ENTRY_NEW, filterEntries, initialState, reduce, titleCase,
} from "../extension/picker.js";

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
