// picker.js — the "which directory?" popup.
//
// Shown when the match is below the auto-file threshold, or when the toast's
// `change` is clicked. Keyboard-first: type to filter, arrows to move, Enter to
// accept, Esc to leave it where it is (the catch-all directory).
//
// The top entry is always the NEW-DIRECTORY proposal (decision D6) — one
// keypress to create, but never created silently. Proposals are Title Case
// (D7); existing directories are never renamed.
//
// The reducer below is pure and node-tested; the DOM wiring at the bottom is a
// thin shell around it.

import { isSafeDirName } from "./sanitize.js";
import { contentTokens, normKey } from "./route_core.js";

export const ENTRY_NEW = "new";
export const ENTRY_DIR = "dir";

/** Title Case a free-typed name for a new-directory proposal. */
export function titleCase(phrase) {
  return String(phrase).split(/\s+/).filter(Boolean).map((word) => {
    const i = [...word].findIndex((c) => /\p{L}/u.test(c));
    if (i < 0) return word;
    return word.slice(0, i) + word[i].toUpperCase() + word.slice(i + 1);
  }).join(" ");
}

/**
 * Build the entry list for a query.
 * `dirs` is the array of existing directory names.
 */
export function filterEntries(dirs, query, suggestNew) {
  const q = String(query || "").trim();
  const qKey = normKey(q);
  const existing = new Set(dirs || []);

  const matches = (name) => {
    if (!qKey) return true;
    const key = normKey(name);
    if (key.includes(qKey)) return true;
    // Also allow token-prefix matching so "jd" finds nothing but "doe" finds
    // "Jane Doe" regardless of word order.
    return contentTokens(name).some((t) => t.startsWith(qKey));
  };

  const rank = (name) => {
    const key = normKey(name);
    if (!qKey) return 2;
    if (key === qKey) return 0;
    if (key.startsWith(qKey)) return 1;
    return 2;
  };

  const dirEntries = (dirs || [])
    .filter(matches)
    .sort((a, b) => (rank(a) - rank(b)) || a.localeCompare(b))
    .map((name) => ({ kind: ENTRY_DIR, name, label: name }));

  const proposalRaw = q ? titleCase(q) : String(suggestNew || "");
  if (!proposalRaw || !isSafeDirName(proposalRaw) || existing.has(proposalRaw)) {
    return dirEntries;
  }
  const proposal = {
    kind: ENTRY_NEW,
    name: proposalRaw,
    label: `+ new dir "${proposalRaw}"`,
  };
  // Placement of the new-directory entry:
  //   * no query      -> TOP. This is decision D6's "pre-filled top entry":
  //                      the matcher's own proposal, one keypress to create.
  //   * query, no hits -> TOP. Nothing else to pick, so it is the obvious
  //                      default.
  //   * query with hits -> BOTTOM. Otherwise typing "smith" and pressing Enter
  //                      would CREATE "Smith" instead of selecting the
  //                      "john-smith" being filtered for. Creation still takes
  //                      one keypress; it just stops being the accidental
  //                      default when an existing directory matches.
  const onTop = !q || dirEntries.length === 0;
  return onTop ? [proposal, ...dirEntries] : [...dirEntries, proposal];
}

export function initialState({ dirs = [], suggestNew = "", downloadId = null,
  reason = "", dup = "", otherDir = "other" } = {}) {
  const state = {
    dirs, suggestNew, downloadId, reason, dup, otherDir,
    query: "", index: 0, done: null,
  };
  state.entries = filterEntries(dirs, "", suggestNew);
  return state;
}

/**
 * Pure reducer. Returns the NEXT state; `state.done` is set to the outcome
 * ({action:"choose"|"cancel", ...}) once the flow finishes.
 */
export function reduce(state, event) {
  if (!event || state.done) return state;
  const next = { ...state };
  if (event.type === "input") {
    next.query = String(event.value ?? "");
    next.entries = filterEntries(state.dirs, next.query, state.suggestNew);
    next.index = 0;
    return next;
  }
  if (event.type === "key") {
    const key = event.key;
    if (key === "ArrowDown") {
      next.index = Math.min(state.index + 1, Math.max(state.entries.length - 1, 0));
      return next;
    }
    if (key === "ArrowUp") {
      next.index = Math.max(state.index - 1, 0);
      return next;
    }
    if (key === "Enter") {
      const entry = state.entries[state.index];
      if (!entry) return state;
      next.done = {
        action: "choose",
        dir: entry.name,
        createdNew: entry.kind === ENTRY_NEW,
      };
      return next;
    }
    if (key === "Escape") {
      // Leave it where the ladder put it — the catch-all directory. No move,
      // no alias, no surprise.
      next.done = { action: "cancel", dir: state.otherDir };
      return next;
    }
  }
  return state;
}

// --- DOM shell ------------------------------------------------------------- //
export function mount(doc, chromeApi) {
  const params = new URLSearchParams(doc.location.search);
  const downloadId = Number(params.get("id"));
  let state = initialState({
    dirs: [],
    suggestNew: params.get("suggestNew") || "",
    downloadId,
    reason: params.get("reason") || "",
    dup: params.get("dup") || "",
  });

  const input = doc.getElementById("q");
  const list = doc.getElementById("list");
  const meta = doc.getElementById("meta");
  const dup = doc.getElementById("dup");

  meta.textContent = state.reason;
  if (state.dup) dup.textContent = state.dup;

  const render = () => {
    list.textContent = "";
    state.entries.forEach((entry, i) => {
      const li = doc.createElement("li");
      li.textContent = entry.label;
      li.className = i === state.index ? "row sel" : "row";
      if (entry.kind === ENTRY_NEW) li.classList.add("new");
      list.appendChild(li);
    });
  };

  const finish = async () => {
    const done = state.done;
    if (!done) return;
    if (done.action === "choose") {
      await chromeApi.runtime.sendMessage({
        type: "dlr:choose",
        downloadId,
        dir: done.dir,
        createdNew: done.createdNew,
      });
    }
    window.close();
  };

  const apply = (event) => {
    state = reduce(state, event);
    render();
    if (state.done) void finish();
  };

  input.addEventListener("input", () => apply({ type: "input", value: input.value }));
  doc.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key)) {
      e.preventDefault();
      apply({ type: "key", key: e.key });
    }
  });

  chromeApi.runtime.sendMessage({ type: "dlr:snapshot" }, (resp) => {
    const dirs = (resp?.snapshot?.dirs || []).map((d) => d.name);
    state = { ...state, dirs, entries: filterEntries(dirs, state.query, state.suggestNew) };
    render();
  });
  render();
  input.focus();
}

if (typeof document !== "undefined" && typeof chrome !== "undefined"
    && !globalThis.DL_ROUTER_NO_AUTOSTART) {
  mount(document, chrome);
}
