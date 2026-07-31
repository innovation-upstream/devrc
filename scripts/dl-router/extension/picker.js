// picker.js -- the "which directory?" popup.
//
// Shown when the match is below the auto-file threshold, or when the toast's
// `change` is clicked. Keyboard-first: type to filter, arrows to move, Enter to
// accept, Esc to leave it where it is (the catch-all directory).
//
// The top entry is always the NEW-DIRECTORY proposal (decision D6) -- one
// keypress to create, but never created silently. Proposals are Title Case
// (D7); existing directories are never renamed.
//
// The reducer below is pure and node-tested; the DOM wiring at the bottom is a
// thin shell around it.

import { isSafeDirName } from "./sanitize.js";
import { contentTokens, normKey } from "./route_core.js";

export const ENTRY_NEW = "new";
export const ENTRY_DIR = "dir";
export const ENTRY_KIND = "kind";

// The two directory kinds, and the one-line explanation of each. Creating a
// directory without saying which it is leaves it UNCLASSIFIED, and an
// unclassified directory never auto-files -- so it would silently interrupt
// every future download into it. Hence a second keypress, not an assumption.
export const KIND_CHOICES = [
  { name: "performer", label: "performer - a person or group (may auto-file)" },
  { name: "category", label: "category - a topic (always asks first)" },
];

export function kindEntries() {
  return KIND_CHOICES.map((k) => ({ kind: ENTRY_KIND, name: k.name,
    label: k.label }));
}

// How hard mount() tries to get the directory list before giving up. A cold
// service worker needs one storage read; these bounds cover a slow wake without
// leaving the window unusable forever.
export const SNAPSHOT_ATTEMPTS = 8;
export const SNAPSHOT_RETRY_MS = 300;

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
 * `counts` is an optional {name -> number} map of how many files each holds.
 *
 * The count rides as its own field rather than being appended to `label`:
 * `label` is what the new-directory proposal is built from and what the
 * sidecar-facing flow reads, and a decorative number has no business in it.
 */
export function filterEntries(dirs, query, suggestNew, counts) {
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

  const countOf = (name) => {
    const n = counts && counts[name];
    return typeof n === "number" && Number.isFinite(n) && n >= 0 ? n : undefined;
  };

  const dirEntries = (dirs || [])
    .filter(matches)
    .sort((a, b) => (rank(a) - rank(b)) || a.localeCompare(b))
    .map((name) => ({ kind: ENTRY_DIR, name, label: name,
      count: countOf(name) }));

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
  reason = "", dup = "", otherDir = "other", loading = false,
  counts = null } = {}) {
  const state = {
    dirs, suggestNew, downloadId, reason, dup, otherDir, counts,
    // `loading` = the directory list has not arrived from the sidecar yet.
    // While it is set, Enter is DEFERRED rather than acted on: see reduce().
    loading: Boolean(loading),
    failed: false,
    error: "",
    status: "",
    pendingEnter: false,
    // The name of a directory the user asked to CREATE, while we ask which
    // kind it is. Non-null means the list on screen is the kind prompt.
    pendingNew: null,
    query: "", index: 0, done: null,
  };
  state.entries = filterEntries(dirs, "", suggestNew, counts);
  return state;
}

/**
 * Pure reducer. Returns the NEXT state; `state.done` is set to the outcome
 * ({action:"choose"|"cancel", ...}) once the flow finishes.
 */
export function reduce(state, event) {
  if (!event) return state;
  // BEFORE the `state.done` guard on purpose: this event exists to UNDO
  // `done`, and the guard would drop it -- which made apply() see `done` still
  // set, call finish() again, get refused again, and spin forever (OOM in the
  // test runner within seconds).
  if (event.type === "choosing") {
    // ABOVE the `state.done` guard, for the same reason choose-failed is: it
    // is dispatched while `done` is set, and the guard would drop it.
    return {
      ...state,
      status: `Filing into "${String(event.dir)}"...`,
      error: "",
    };
  }
  if (event.type === "choose-failed") {
    // The service worker refused the pick (most often /relocate could not
    // prove this router created the file). finish() used to await
    // sendMessage, DISCARD the response and close the window regardless, so
    // an immediate refusal was never seen by anyone -- and every `choose`
    // goes through here. Clearing `done` leaves the window usable: the user
    // can pick a different directory, or Esc out.
    return {
      ...state,
      done: null,
      status: "",
      error: String(event.message || "The sidecar refused that choice."),
    };
  }
  if (state.done) return state;
  // --- click ------------------------------------------------------------- //
  // Mouse selection, expressed as "move the highlight there, then Enter" so it
  // reuses the Enter branch VERBATIM: the kind prompt, the new-directory
  // sub-question, the failed/loading refusals and the `done` shape all come
  // from one implementation. A second copy of "what does choosing mean" is
  // exactly the drift this file already paid for once.
  //
  // Placed here -- after the `done` guard, before the kind prompt -- so it is
  // dead once the flow has finished, and live while the kind prompt is up
  // (clicking "performer" must work).
  if (event.type === "click") {
    const i = event.index;
    if (!Number.isInteger(i) || i < 0 || i >= state.entries.length) return state;
    // A click while the list is unavailable is DROPPED, not deferred.
    //
    // Enter defers (see `pendingEnter`) because a typed query survives the
    // list arriving -- the user's intent is "the thing matching what I typed".
    // A click's intent is a SCREEN POSITION, and position N of the loading
    // placeholder has nothing to do with position N of the real list. Honouring
    // it later would choose an arbitrary directory, or create one, which is the
    // precise footgun the deferred Enter exists to close.
    if (state.loading || state.failed) return state;
    return reduce({ ...state, index: i }, { type: "key", key: "Enter" });
  }
  const next = { ...state };
  // --- the kind prompt --------------------------------------------------- //
  // A modal sub-step, handled BEFORE the normal branches so a stray `input`
  // (the text box keeps focus) cannot rebuild the list out from under it.
  if (state.pendingNew) {
    if (event.type !== "key") return state;
    if (event.key === "ArrowDown") {
      next.index = Math.min(state.index + 1, state.entries.length - 1);
      return next;
    }
    if (event.key === "ArrowUp") {
      next.index = Math.max(state.index - 1, 0);
      return next;
    }
    if (event.key === "Escape") {
      // Back to the directory list, NOT out of the picker. Escaping a
      // sub-question should undo the sub-question.
      next.pendingNew = null;
      next.entries = filterEntries(state.dirs, state.query, state.suggestNew,
        state.counts);
      next.index = 0;
      return next;
    }
    if (event.key === "Enter") {
      const choice = state.entries[state.index];
      if (!choice) return state;
      next.pendingNew = null;
      next.done = { action: "choose", dir: state.pendingNew,
        createdNew: true, kind: choice.name };
      return next;
    }
    return state;
  }
  if (event.type === "input") {
    // Typing is a new attempt: the previous refusal is no longer what the
    // screen is about. It used to persist through the retype AND through the
    // next in-flight choose, with no progress shown.
    next.error = "";
    next.status = "";
    next.query = String(event.value ?? "");
    next.entries = filterEntries(state.dirs, next.query, state.suggestNew,
      state.counts);
    next.index = 0;
    return next;
  }
  if (event.type === "dirs-failed") {
    // Could not get the list at all. Stay OUT of the "loading" state so the
    // window is not stuck, but do NOT pretend the library is empty: a pending
    // Enter is dropped rather than silently turned into a directory creation.
    next.loading = false;
    next.failed = true;
    next.pendingEnter = false;
    next.entries = [];
    return next;
  }
  if (event.type === "dirs") {
    // The directory list arrived. Rebuild the entries against the CURRENT
    // query, then honour an Enter the user pressed while we were still empty.
    next.dirs = Array.isArray(event.dirs) ? event.dirs : [];
    // Counts are cosmetic and optional: a snapshot without them (an older
    // sidecar, or a file index that has not been walked yet) renders exactly
    // as it always did rather than showing zeroes, which would be a lie.
    next.counts = (event.counts && typeof event.counts === "object")
      ? event.counts : null;
    next.loading = false;
    next.entries = filterEntries(next.dirs, state.query, state.suggestNew,
      next.counts);
    next.index = 0;
    if (state.pendingEnter) {
      next.pendingEnter = false;
      return reduce(next, { type: "key", key: "Enter" });
    }
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
      // Nothing may be chosen when the directory list could not be loaded --
      // "no answer" is not "no directories exist".
      if (state.failed) return state;
      // THE RACE. mount() starts with `dirs: []` and fills it from an async
      // `dlr:snapshot` round-trip, and the window opens FOCUSED. Typing a query
      // and hitting Enter before the answer landed used to CREATE a new
      // directory -- because with no directories loaded, filterEntries has
      // nothing to match and the "+ new dir" proposal is the only entry, on
      // top. That is precisely the footgun the entry-ordering rule above was
      // written to close, re-opened by the load order.
      //
      // Deferring rather than dropping the keypress: the user's Enter is
      // honoured the moment the real list arrives, against the list they
      // actually meant.
      if (state.loading) {
        next.pendingEnter = true;
        return next;
      }
      const entry = state.entries[state.index];
      if (!entry) return state;
      next.error = "";
      if (entry.kind === ENTRY_NEW) {
        // One more question before anything is created.
        next.pendingNew = entry.name;
        next.entries = kindEntries();
        next.index = 0;
        return next;
      }
      next.done = {
        action: "choose",
        dir: entry.name,
        createdNew: false,
      };
      return next;
    }
    if (key === "Escape") {
      // Leave it where the ladder put it -- the catch-all directory. No move,
      // no alias, no surprise.
      next.done = { action: "cancel", dir: state.otherDir };
      return next;
    }
  }
  return state;
}

// --- DOM shell ------------------------------------------------------------- //
/**
 * Wire the reducer to the page. Returns a small handle so the flow is
 * drivable in tests (it had 0% coverage, which is how the load-order race
 * above survived a suite that covered the reducer thoroughly).
 *
 * `closeWindow` is injectable for the same reason.
 */
export function mount(doc, chromeApi, { closeWindow } = {}) {
  const params = new URLSearchParams(doc.location.search);
  const rawId = params.get("id") || "";
  // A yt-dlp job id is a string ("fetch:3"); a browser download id is a number.
  const downloadId = /^\d+$/.test(rawId) ? Number(rawId) : rawId;
  let state = initialState({
    dirs: [],
    suggestNew: params.get("suggestNew") || "",
    downloadId,
    reason: params.get("reason") || "",
    dup: params.get("dup") || "",
    // Nothing may be CHOSEN until the real directory list has arrived.
    loading: true,
  });

  // Rendered as an in-page overlay (an iframe of THIS page inside a closed
  // shadow root in the tab) rather than as a popup window. The document, the
  // reducer and every branch below are identical; only "what does closing
  // mean" differs, because an iframe cannot close itself.
  const embedded = params.get("embed") === "1";
  const overlayId = params.get("overlay") || "";

  const input = doc.getElementById("q");
  const list = doc.getElementById("list");
  const meta = doc.getElementById("meta");
  const dup = doc.getElementById("dup");

  const renderMeta = () => {
    // `status` first: it is the only one describing something happening NOW.
    // Writing it straight to `meta.textContent` instead meant the next
    // render() erased it -- and any event during an in-flight choose triggers
    // one, so an Escape mid-choose left a BLANK meta line on a picker that is
    // (correctly) unresponsive until the choose settles.
    meta.textContent = state.pendingNew
      ? `New directory "${state.pendingNew}" - which kind? (Esc to go back)`
      : (state.status || state.error || state.reason);
    if (state.dup) dup.textContent = state.dup;
  };
  renderMeta();

  const close = closeWindow || (() => {
    if (embedded) {
      // window.close() on an iframe is a no-op, so an embedded picker asks the
      // service worker to tear the overlay down -- it is the side that knows
      // which tab the overlay was injected into. Every other close path
      // (accepted, cancelled, refused-then-Esc) funnels through here, so the
      // overlay cannot outlive the flow.
      // `.catch` as well as `try`: nothing answers this message, so Chrome
      // settles the promise with "the message port closed before a response
      // was received". Unhandled, that is a red error in the picker's console
      // on EVERY successful pick.
      try {
        const sent = chromeApi.runtime.sendMessage({
          type: "dlr:picker-closed", overlay: overlayId });
        if (sent && typeof sent.catch === "function") sent.catch(() => {});
      } catch { /* worker restarting; the overlay is inert either way */ }
      return;
    }
    if (typeof window !== "undefined") window.close();
  });

  const render = () => {
    renderMeta();
    list.textContent = "";
    if (state.loading || state.failed) {
      const li = doc.createElement("li");
      li.textContent = state.failed
        ? "Could not reach the sidecar. Enter to retry, Esc to leave it."
        : "Loading directories...";
      li.className = state.failed ? "row failed" : "row loading";
      list.appendChild(li);
      return;
    }
    // The row the flow settled on. Both Enter and a click act on
    // `state.index` -- the click event moves the highlight there first -- so
    // the confirmation lands on what the user picked from either input, with
    // no second notion of "the chosen row" to drift.
    const takenIndex = (state.done && state.done.action === "choose")
      ? state.index : -1;
    state.entries.forEach((entry, i) => {
      const li = doc.createElement("li");
      const label = doc.createElement("span");
      label.className = "label";
      label.textContent = entry.label;
      li.appendChild(label);
      if (typeof entry.count === "number") {
        // How many files the directory already holds. Decorative, and a
        // separate node so it can be styled down and can never be mistaken
        // for part of the directory name.
        const badge = doc.createElement("span");
        badge.className = "count";
        badge.textContent = String(entry.count);
        li.appendChild(badge);
      }
      li.className = i === state.index ? "row sel" : "row";
      if (entry.kind === ENTRY_NEW) li.classList.add("new");
      if (i === takenIndex) li.classList.add("taken");
      // Mouse selection. `apply` is declared below; the handler only ever runs
      // after mount() has returned, so the reference resolves.
      li.addEventListener("click", () => apply({ type: "click", index: i }));
      list.appendChild(li);
    });
  };

  const finish = async () => {
    const done = state.done;
    if (!done) return;
    if (done.action === "choose") {
      apply({ type: "choosing", dir: done.dir });
      let resp;
      try {
        const message = {
          type: "dlr:choose",
          downloadId,
          dir: done.dir,
          createdNew: done.createdNew,
        };
        // Only on creation, and only ever set by the kind prompt -- so
        // selecting an existing directory sends exactly the message it always
        // did, with no `kind: undefined` riding along.
        if (done.kind) message.kind = done.kind;
        // The per-open id, ONLY when embedded. `picker.html` is
        // web-accessible, so any page can frame it and try to clickjack a pick
        // out of the user; the worker refuses a choice from a SUBFRAME that
        // cannot present an id it issued. The popup window is a top-level frame
        // and deliberately still sends the message it always did.
        if (embedded && overlayId) message.overlay = overlayId;
        resp = await chromeApi.runtime.sendMessage(message);
      } catch (err) {
        // `err.message`, not String(err): String() prepends "Error: ", the
        // exact shape just removed from the sidecar path.
        resp = { ok: false, error: (err && err.message) || String(err) };
      }
      // A refusal must be VISIBLE. The window stays open so the user can pick
      // somewhere else or press Esc -- closing on a rejected move is how an
      // immediate refusal became invisible.
      //
      // A MISSING answer counts as a refusal too. Chrome's sendMessage
      // behaviour when the service worker is torn down mid-choose is not
      // something this code can verify, and treating `undefined` as success
      // would close the window on an unknown outcome -- exactly the silent
      // close this branch exists to prevent.
      if (!resp || resp.ok === false) {
        apply({ type: "choose-failed",
                message: `Could not file into "${done.dir}": `
                         + `${(resp && resp.error) || "no answer from the "
                         + "extension (it may have been restarted)"}` });
        return;
      }
    }
    close();
  };

  // SINGLE-FLIGHT AND LATCHED. `reduce` correctly returns state unchanged once
  // `done` is set, but `apply` then ran `if (state.done) void finish()`
  // regardless -- so every keypress during an in-flight choose fired ANOTHER
  // `dlr:choose`. Three keypresses -> four messages.
  //
  // An in-flight flag alone was not enough: it reset in `.finally()` while
  // `done` stayed set on the success and cancel paths, so once the choose
  // SETTLED every later event re-entered finish() and re-sent. Measured on
  // that version: 1 Enter + 3 keys -> 4 messages; 1 Enter + 1 keystroke -> 2.
  // It was masked on the happy path only by close() tearing the page down,
  // and whether window.close() on a popup is instant has never been observed
  // in a real browser.
  //
  // So the flag LATCHES: it stays true exactly while `done` is set. The
  // refusal path clears `done` (that is what choose-failed is for), which
  // unlatches it and lets the user retry; the closing paths leave `done` set
  // and stay latched forever.
  let finishing = false;
  const apply = (event) => {
    state = reduce(state, event);
    render();
    if (state.done && !finishing) {
      finishing = true;
      void finish()
        .finally(() => { finishing = Boolean(state.done); })
        .catch(() => { /* finish() reports its own failures */ });
    }
  };

  input.addEventListener("input", () => apply({ type: "input", value: input.value }));
  doc.addEventListener("keydown", (e) => {
    if (!["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key)) return;
    e.preventDefault();
    // Enter on the "could not reach the sidecar" screen retries, rather than
    // leaving the window Esc-only forever.
    if (e.key === "Enter" && state.failed) {
      state = { ...state, failed: false, loading: true, error: "" };
      render();
      attempts = 0;
      askForDirs();
      return;
    }
    apply({ type: "key", key: e.key });
  });

  // Asking for the snapshot, with retries.
  //
  // A FAILED answer is not an empty library. If the service worker was cold
  // when this popup asked, its token was still `""` and the /dirs fetch 401'd;
  // treating that as "there are no directories" makes the new-directory
  // proposal the only entry, so typing a name and pressing Enter CREATES one
  // instead of selecting the existing match -- the exact footgun the deferred
  // Enter above exists to prevent, re-entered through the back door.
  //
  // So: only leave the loading state on a snapshot we actually got. `ok` is
  // false when the worker could not answer; a genuinely empty library answers
  // ok with an empty list, and that does clear loading.
  let attempts = 0;
  const askForDirs = () => {
    attempts += 1;
    chromeApi.runtime.sendMessage({ type: "dlr:snapshot" }, (resp) => {
      const usable = Boolean(resp && resp.ok && resp.snapshot
        && Array.isArray(resp.snapshot.dirs));
      if (!usable) {
        if (attempts < SNAPSHOT_ATTEMPTS) {
          setTimeout(askForDirs, SNAPSHOT_RETRY_MS);
        } else {
          apply({ type: "dirs-failed" });
        }
        return;
      }
      const dirs = resp.snapshot.dirs
        .map((d) => d && d.name)
        .filter((n) => typeof n === "string" && n);
      apply({ type: "dirs", dirs, counts: resp.snapshot.counts });
    });
  };
  askForDirs();
  render();
  input.focus();

  if (embedded) {
    // THE OVERLAY'S PROOF OF LIFE, and it is not decorative.
    //
    // The content script can create the host element and the iframe and still
    // end up with a frame that never loaded: a content-script-injected iframe
    // is subject to the PAGE's Content-Security-Policy, so a site with a
    // restrictive `frame-src` blocks it outright. From the outside that failure
    // is indistinguishable from success -- an overlay that is present, empty
    // and silent -- and the download would be left with no picker at all.
    //
    // This message is the only signal that an extension context actually
    // booted inside the frame. The service worker will not consider the
    // overlay delivered without it, and falls back to the popup window.
    try {
      const sent = chromeApi.runtime.sendMessage({
        type: "dlr:picker-ready", overlay: overlayId });
      if (sent && typeof sent.catch === "function") sent.catch(() => {});
    } catch { /* the worker's timeout falls back to a window */ }
  }

  return { state: () => state, apply, retry: askForDirs };
}

if (typeof document !== "undefined" && typeof chrome !== "undefined"
    && !globalThis.DL_ROUTER_NO_AUTOSTART) {
  mount(document, chrome);
}
