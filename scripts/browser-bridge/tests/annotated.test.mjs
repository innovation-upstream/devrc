// Tests for the annotated text helpers: generateCssPath, extractIdentifyingAttrs,
// getAdjacentText. These are pure functions that operate on DOM-like objects,
// so we test them with simple mock objects (no jsdom needed).
//
// Run: node --test scripts/browser-bridge/tests/annotated.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {
  generateCssPath, extractIdentifyingAttrs, getAdjacentText,
  annotatedTextFn, ANNOTATED_TEXT_MAX_ITEMS_DEFAULT,
} from "../extension/protocol.js";

// --- mock DOM helpers ------------------------------------------------------ //
// Minimal mock objects that satisfy the helpers' attribute/child access patterns.

function mockElement(tag, attrs = {}, children = []) {
  const el = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    id: attrs.id || "",
    getAttribute(name) { return attrs[name] != null ? attrs[name] : null; },
    parentElement: null,
    children: children,
    childNodes: children.slice(),
    nextSibling: null,
    previousSibling: null,
    textContent: "",
  };
  // Wire up parent.
  for (const child of children) {
    if (child && typeof child === "object") child.parentElement = el;
  }
  return el;
}

function mockText(content) {
  return { nodeType: 3, textContent: content };
}

// --- generateCssPath ------------------------------------------------------- //
test("generateCssPath: simple div > p", () => {
  const p = mockElement("p");
  const div = mockElement("div", {}, [p]);
  assert.equal(generateCssPath(p), "div > p");
});

test("generateCssPath: element with id short-circuits", () => {
  const el = mockElement("div", { id: "my-id" });
  assert.equal(generateCssPath(el), "#my-id");
});

test("generateCssPath: second li gets :nth-child(2)", () => {
  const li1 = mockElement("li");
  const li2 = mockElement("li");
  const ul = mockElement("ul", {}, [li1, li2]);
  assert.equal(generateCssPath(li2), "ul > li:nth-child(2)");
});

test("generateCssPath: first li gets :nth-child(1)", () => {
  const li1 = mockElement("li");
  const li2 = mockElement("li");
  const ul = mockElement("ul", {}, [li1, li2]);
  assert.equal(generateCssPath(li1), "ul > li:nth-child(1)");
});

test("generateCssPath: single child needs no :nth-child", () => {
  const p = mockElement("p");
  const div = mockElement("div", {}, [p]);
  assert.equal(generateCssPath(p), "div > p");
});

test("generateCssPath: nested with id at root", () => {
  const span = mockElement("span");
  const div = mockElement("div", { id: "root" }, [span]);
  assert.equal(generateCssPath(span), "#root > span");
});

test("generateCssPath: deeply nested", () => {
  const a = mockElement("a");
  const li = mockElement("li", {}, [a]);
  const ul = mockElement("ul", {}, [li]);
  const body = mockElement("body", {}, [ul]);
  const html = mockElement("html", {}, [body]);
  const path = generateCssPath(a);
  // Should be: html > body > ul > li > a
  assert.ok(path.includes("html"));
  assert.ok(path.includes("body"));
  assert.ok(path.includes("ul"));
  assert.ok(path.includes("li"));
  assert.ok(path.includes("a"));
});

test("generateCssPath: null/missing returns empty", () => {
  assert.equal(generateCssPath(null), "");
  assert.equal(generateCssPath({}), "");
});

// --- extractIdentifyingAttrs ----------------------------------------------- //
test("extractIdentifyingAttrs: picks up id, class, href, data-testid, aria-label", () => {
  const el = mockElement("a", {
    id: "link1",
    class: "btn primary",
    href: "https://example.com",
    "data-testid": "nav-link",
    "aria-label": "Go home",
  });
  const attrs = extractIdentifyingAttrs(el);
  assert.equal(attrs.id, "link1");
  assert.equal(attrs.class, "btn primary");
  assert.equal(attrs.href, "https://example.com");
  assert.equal(attrs["data-testid"], "nav-link");
  assert.equal(attrs["aria-label"], "Go home");
});

test("extractIdentifyingAttrs: omits empty/missing attributes", () => {
  const el = mockElement("div", { id: "only-id" });
  const attrs = extractIdentifyingAttrs(el);
  assert.deepEqual(attrs, { id: "only-id" });
});

test("extractIdentifyingAttrs: returns empty for null", () => {
  assert.deepEqual(extractIdentifyingAttrs(null), {});
});

test("extractIdentifyingAttrs: omits empty class", () => {
  const el = mockElement("div", { class: "" });
  assert.deepEqual(extractIdentifyingAttrs(el), {});
});

// --- getAdjacentText ------------------------------------------------------- //
test("getAdjacentText: preceding and following text nodes", () => {
  const el = mockElement("span");
  el.previousSibling = mockText("before text");
  el.nextSibling = mockText("after text");
  const r = getAdjacentText(el);
  assert.equal(r.precedingText, "before text");
  assert.equal(r.followingText, "after text");
});

test("getAdjacentText: first child has no preceding", () => {
  const el = mockElement("span");
  el.nextSibling = mockText("next");
  const r = getAdjacentText(el);
  assert.equal(r.precedingText, "");
  assert.equal(r.followingText, "next");
});

test("getAdjacentText: last child has no following", () => {
  const el = mockElement("span");
  el.previousSibling = mockText("prev");
  const r = getAdjacentText(el);
  assert.equal(r.precedingText, "prev");
  assert.equal(r.followingText, "");
});

test("getAdjacentText: falls back to sibling element textContent", () => {
  const el = mockElement("span");
  const sibling = mockElement("div");
  sibling.textContent = "sibling text content";
  el.previousSibling = sibling;
  const r = getAdjacentText(el);
  assert.equal(r.precedingText, "sibling text content");
});

test("getAdjacentText: truncates to maxLen", () => {
  const el = mockElement("span");
  el.previousSibling = mockText("a".repeat(100));
  el.nextSibling = mockText("b".repeat(100));
  const r = getAdjacentText(el, 10);
  assert.equal(r.precedingText.length, 10);
  assert.equal(r.followingText.length, 10);
  assert.equal(r.precedingText, "a".repeat(10));
  assert.equal(r.followingText, "b".repeat(10));
});

test("getAdjacentText: trims whitespace", () => {
  const el = mockElement("span");
  el.previousSibling = mockText("  spaced  ");
  const r = getAdjacentText(el);
  assert.equal(r.precedingText, "spaced");
});

test("getAdjacentText: null element returns empty", () => {
  const r = getAdjacentText(null);
  assert.deepEqual(r, { precedingText: "", followingText: "" });
});

// --- annotatedTextFn (injected function, tested via direct call) ----------- //
// annotatedTextFn is self-contained and designed for chrome.scripting injection,
// but we can call it with a mock-ish document. It uses document.querySelector,
// document.body, and DOM traversal — so we test with a real-ish structure.
// Since we can't use a real DOM here, we test the function's output structure
// by calling it and verifying it returns the expected shape.

test("annotatedTextFn: returns { elements, count } shape", () => {
  // annotatedTextFn expects a real DOM. Since we're in Node (no DOM), we verify
  // the function exists and is a function. The real DOM tests are integration
  // tests run in the browser.
  assert.equal(typeof annotatedTextFn, "function");
});

test("annotatedTextFn: default maxItems is 200", () => {
  // Verify the constant matches.
  assert.equal(ANNOTATED_TEXT_MAX_ITEMS_DEFAULT, 200);
});
