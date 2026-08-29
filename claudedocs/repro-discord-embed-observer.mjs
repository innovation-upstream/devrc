// Reproduction: does the MutationObserver survive a mutation batch that contains no media?
//
// v0.2.2 added "observer disconnects during style changes", reconnecting only `if (found > 0)`.
// On Discord, most DOM churn (typing indicators, scroll, presence) adds NO media, so the very
// first such batch should leave the observer permanently disconnected.
//
// Run with the file under test as argv[2]. Controls both ways:
//   - the WIP working-tree copy (v0.2.3)  -> expect DISCONNECTED (the defect)
//   - origin/main's copy (v0.1.0)         -> expect STILL CONNECTED (harness can tell them apart)

import { readFileSync } from "node:fs";
import { makeDiscordDoc, FakeElement } from "/home/zach/workspace/devrc/scripts/discord-embed-ext/tests/fake_discord_dom.mjs";

const target = process.argv[2];
const label = process.argv[3] || target;
const source = readFileSync(target, "utf8");

// Instrumented observer: the real one tracks connection state; FakeMutationObserver's
// observe/disconnect are no-ops, so it cannot see this defect at all.
let live = null;
class TrackingMutationObserver {
  constructor(cb) {
    this._cb = cb;
    this.connected = false;
    this.observeCalls = 0;
    this.disconnectCalls = 0;
    live = this;
  }
  observe() { this.connected = true; this.observeCalls++; }
  disconnect() { this.connected = false; this.disconnectCalls++; }
  takeRecords() { return []; }
  // Deliver a batch the way the browser would: ONLY if currently connected.
  deliver(addedNodes) {
    if (!this.connected) return false;
    this._cb([{ addedNodes }]);
    return true;
  }
}
globalThis.MutationObserver = TrackingMutationObserver;

const fakeGlobal = { DEE_NO_AUTOSTART: true };
new Function("globalThis", "document", source)(fakeGlobal, undefined);
const DEE = fakeGlobal.__DEE__;

const doc = makeDiscordDoc("<html><head></head><body></body></html>");
DEE.observe(doc);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

console.log(`=== ${label} ===`);
console.log(`after observe():            connected=${live.connected}`);

// The shipped FakeElement has no `nodeType`, but the observer callback gates on
// `node.nodeType === 1`. Without this the callback skips every node and the run is vacuous.
const el = (tag, attrs, kids) => Object.assign(new FakeElement(tag, attrs, kids || []), { nodeType: 1 });

// Batch 1: ordinary Discord churn with NO media — a typing indicator.
const noise = el("div", { class: "typingIndicator_abc" });
const delivered1 = live.deliver([noise]);
await sleep(250); // > DEBOUNCE_MS (100)
console.log(`after a NO-MEDIA batch:     connected=${live.connected}  (delivered=${delivered1}, observe=${live.observeCalls}, disconnect=${live.disconnectCalls})`);

// Batch 2: a real attachment arrives afterwards. A disconnected observer never sees it.
const img = el("img", { src: "https://cdn.discordapp.com/attachments/1/2/photo.png" });
const wrapper = el("div", { class: "imageWrapper_x" }, [img]);
const delivered2 = live.deliver([wrapper]);
await sleep(250);
const marked = img.getAttribute("data-dee-enlarged");
console.log(`later REAL attachment:      batch_seen=${delivered2}  enlarged=${marked === "1"}`);
console.log(
  marked === "1"
    ? "VERDICT: media still enlarged — observer survived."
    : "VERDICT: media NOT enlarged — the observer was left disconnected."
);
console.log("");
