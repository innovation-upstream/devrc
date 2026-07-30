// A tiny HTML parser + CSS selector engine, so the content-capture extractors
// can be driven over REAL fixture HTML with no browser and no dependency.
//
// It supports exactly the selector grammar dl-router uses:
//   tag, .class, #id, [attr], [attr="v"], [attr^="v"], descendant combinators,
//   and comma-separated groups.
// Anything richer is out of scope on purpose — the per-site rules table is
// documented as supporting this subset.

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img",
  "input", "link", "meta", "param", "source", "track", "wbr"]);
const RAW_TEXT = new Set(["script", "style"]);

class El {
  constructor(tagName, attrs = {}) {
    this.tagName = tagName;
    this.attrs = attrs;
    this.children = [];
    this.parentElement = null;
    this.text = "";
  }

  getAttribute(name) {
    const v = this.attrs[name.toLowerCase()];
    return v === undefined ? null : v;
  }

  get textContent() {
    if (this.children.length === 0) return this.text;
    return this.text + this.children.map((c) => c.textContent).join("");
  }

  get classList() {
    return (this.getAttribute("class") || "").split(/\s+/).filter(Boolean);
  }
}

function parseAttrs(raw) {
  const attrs = {};
  const re = /([:\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = re.exec(raw))) {
    attrs[m[1].toLowerCase()] = m[2] ?? m[3] ?? m[4] ?? "";
  }
  return attrs;
}

/** Parse fixture HTML into a tree. Tolerant: unclosed tags are fine. */
export function parseHTML(html) {
  const root = new El("#document");
  let current = root;
  let i = 0;
  const push = (node) => {
    node.parentElement = current === root ? null : current;
    current.children.push(node);
  };

  while (i < html.length) {
    const lt = html.indexOf("<", i);
    if (lt < 0) {
      current.text += html.slice(i);
      break;
    }
    if (lt > i) current.text += html.slice(i, lt);

    if (html.startsWith("<!--", lt)) {
      const end = html.indexOf("-->", lt);
      i = end < 0 ? html.length : end + 3;
      continue;
    }
    if (html.startsWith("<!", lt)) {
      const end = html.indexOf(">", lt);
      i = end < 0 ? html.length : end + 1;
      continue;
    }
    const gt = html.indexOf(">", lt);
    if (gt < 0) {
      current.text += html.slice(lt);
      break;
    }
    const inner = html.slice(lt + 1, gt).trim();

    if (inner.startsWith("/")) {
      const name = inner.slice(1).trim().toLowerCase();
      // Close the nearest matching ancestor; ignore a stray close tag.
      let node = current;
      while (node && node !== root && node.tagName !== name) {
        node = node.parentElement || root;
      }
      if (node && node !== root) current = node.parentElement || root;
      i = gt + 1;
      continue;
    }

    const selfClosing = inner.endsWith("/");
    const body = selfClosing ? inner.slice(0, -1) : inner;
    const space = body.search(/\s/);
    const tag = (space < 0 ? body : body.slice(0, space)).toLowerCase();
    const attrs = space < 0 ? {} : parseAttrs(body.slice(space));
    const node = new El(tag, attrs);
    push(node);
    i = gt + 1;

    if (RAW_TEXT.has(tag)) {
      const close = html.toLowerCase().indexOf(`</${tag}`, i);
      node.text = close < 0 ? html.slice(i) : html.slice(i, close);
      i = close < 0 ? html.length : html.indexOf(">", close) + 1;
      continue;
    }
    if (!selfClosing && !VOID.has(tag)) current = node;
  }
  return root;
}

function walk(node, visit) {
  for (const child of node.children) {
    visit(child);
    walk(child, visit);
  }
}

function parseCompound(part) {
  const out = { tag: null, id: null, classes: [], attrs: [] };
  const re = /(\[[^\]]*\]|\.[\w-]+|#[\w-]+|\*|[\w-]+)/g;
  let m;
  while ((m = re.exec(part))) {
    const token = m[1];
    if (token.startsWith("[")) {
      const body = token.slice(1, -1);
      const am = body.match(/^([:\w-]+)\s*(\^=|\$=|\*=|=)?\s*(?:"([^"]*)"|'([^']*)'|(.*))?$/);
      if (am) {
        out.attrs.push({
          name: am[1].toLowerCase(),
          op: am[2] || null,
          value: am[3] ?? am[4] ?? (am[5] === "" ? undefined : am[5]),
        });
      }
    } else if (token.startsWith(".")) out.classes.push(token.slice(1));
    else if (token.startsWith("#")) out.id = token.slice(1);
    else if (token !== "*") out.tag = token.toLowerCase();
  }
  return out;
}

function matchesCompound(node, c) {
  if (c.tag && node.tagName !== c.tag) return false;
  if (c.id && node.getAttribute("id") !== c.id) return false;
  for (const cls of c.classes) {
    if (!node.classList.includes(cls)) return false;
  }
  for (const a of c.attrs) {
    const v = node.getAttribute(a.name);
    if (v === null) return false;
    if (a.op === undefined || a.op === null || a.value === undefined) continue;
    if (a.op === "=" && v !== a.value) return false;
    if (a.op === "^=" && !v.startsWith(a.value)) return false;
    if (a.op === "$=" && !v.endsWith(a.value)) return false;
    if (a.op === "*=" && !v.includes(a.value)) return false;
  }
  return true;
}

function matchesSelector(node, selector) {
  const parts = selector.trim().split(/\s+/).map(parseCompound);
  const last = parts[parts.length - 1];
  if (!matchesCompound(node, last)) return false;
  let ancestor = node.parentElement;
  for (let i = parts.length - 2; i >= 0; i -= 1) {
    let found = false;
    while (ancestor) {
      if (matchesCompound(ancestor, parts[i])) { found = true; break; }
      ancestor = ancestor.parentElement;
    }
    if (!found) return false;
    ancestor = ancestor.parentElement;
  }
  return true;
}

export function queryAll(root, selector) {
  const groups = String(selector).split(",").map((s) => s.trim()).filter(Boolean);
  const out = [];
  walk(root, (node) => {
    if (groups.some((g) => matchesSelector(node, g))) out.push(node);
  });
  return out;
}

/** The adapter shape content_capture.js's extractors expect. */
export function domFromHTML(html, { url = "https://example-site.test/v/1",
  title = "" } = {}) {
  const root = parseHTML(html);
  const titleEl = queryAll(root, "title")[0];
  return {
    querySelectorAll: (sel) => queryAll(root, sel),
    url,
    title: title || (titleEl ? titleEl.textContent.trim() : ""),
    root,
  };
}

/** A single element descriptor, as describeTarget would produce. */
export function element(tagName, attrs = {}, text = "") {
  const el = new El(tagName.toLowerCase(), attrs);
  el.text = text;
  return el;
}
