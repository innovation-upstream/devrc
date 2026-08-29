class FakeComputedStyle {
  constructor() {
    this._props = new Map();
  }
  setProperty(prop, val, priority) {
    this._props.set(prop, { value: val, important: priority === "important" });
  }
  getPropertyValue(prop) {
    var entry = this._props.get(prop);
    return entry ? entry.value : "";
  }
  // 🔴 REAL CSSStyleDeclaration HAS THIS, AND unclipAncestors RESTORES IT.
  // Its absence made the extension's `style.getPropertyPriority ? … : ""`
  // guard take the else-branch in EVERY test, so the recorded priority was
  // always "" and a mutant dropping priority round-tripping survived green.
  getPropertyPriority(prop) {
    var entry = this._props.get(prop);
    return entry && entry.important ? "important" : "";
  }
  removeProperty(prop) {
    var entry = this._props.get(prop);
    this._props.delete(prop);
    return entry ? entry.value : "";
  }
  get cssText() {
    var out = [];
    for (var e of this._props) {
      out.push(e[0] + ": " + e[1].value + (e[1].important ? " !important" : ""));
    }
    return out.join("; ");
  }
}

class FakeElement {
  constructor(tagName, attrs) {
    // 🔴 UPPERCASE, LIKE A REAL HTML DOCUMENT. This used to lowercase, which
    // silently voided every `.toLowerCase()` in the extension: SEVEN separate
    // guards across both content scripts could be deleted with the suite still
    // fully green (four here, three in lightbox.js), and each deletion
    // makes the extension COMPLETELY INERT in Brave, where tagName really is
    // "IMG"/"VIDEO"/"SOURCE". A fixture that normalises away the thing the code
    // normalises is not a test of that code. Node names beginning with "#"
    // (#document, #shadow-root) are not elements and keep their spelling.
    var raw = String(tagName);
    this.tagName = raw.charAt(0) === "#" ? raw : raw.toUpperCase();
    // Real element nodes are nodeType 1. Its absence here meant the extension's
    // `node.nodeType === 1` filter silently rejected every fake node, so an
    // observer test could never have marked anything.
    this.nodeType = 1;
    this.attrs = attrs ? Object.assign({}, attrs) : {};
    this.children = [];
    this.parentElement = null;
    this.style = new FakeComputedStyle();
    this.text = "";
    this.naturalWidth = 0;
    this.naturalHeight = 0;
    this.src = this.attrs.src || "";
    this._shadowRoot = null;
    this._listeners = new Map();
    this._isConnected = false;
    this._ownerDocument = null;
  }

  get parentNode() {
    return this.parentElement;
  }

  get ownerDocument() {
    return this._ownerDocument;
  }

  getAttribute(name) {
    var v = this.attrs[String(name).toLowerCase()];
    return v === undefined ? null : v;
  }

  setAttribute(name, value) {
    this.attrs[String(name).toLowerCase()] = String(value);
    if (String(name).toLowerCase() === "src") this.src = String(value);
  }

  removeAttribute(name) {
    delete this.attrs[String(name).toLowerCase()];
  }

  get textContent() {
    if (this.children.length === 0) return this.text;
    return this.text + this.children.map(function (c) { return c.textContent; }).join("");
  }

  set textContent(v) {
    this.text = String(v || "");
    this.children = [];
  }

  get className() {
    return this.getAttribute("class") || "";
  }

  set className(v) {
    this.setAttribute("class", v);
  }

  get classList() {
    var self = this;
    return {
      add: function () {
        var cls = new Set((self.getAttribute("class") || "").split(/\s+/).filter(Boolean));
        for (var i = 0; i < arguments.length; i++) cls.add(arguments[i]);
        self.setAttribute("class", Array.from(cls).join(" "));
      },
      remove: function () {
        var cls = new Set((self.getAttribute("class") || "").split(/\s+/).filter(Boolean));
        for (var i = 0; i < arguments.length; i++) cls.delete(arguments[i]);
        self.setAttribute("class", Array.from(cls).join(" "));
      },
      contains: function (n) {
        return (self.getAttribute("class") || "").split(/\s+/).indexOf(n) >= 0;
      },
    };
  }

  appendChild(child) {
    this.children.push(child);
    if (child && typeof child === "object") {
      child.parentElement = this;
      if (this._ownerDocument) child._ownerDocument = this._ownerDocument;
    }
    return child;
  }

  removeChild(child) {
    var i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
    if (child) child.parentElement = null;
    return child;
  }

  remove() {
    var parent = this.parentElement;
    if (!parent) return;
    var i = parent.children.indexOf(this);
    if (i >= 0) parent.children.splice(i, 1);
    this.parentElement = null;
  }

  attachShadow(opts) {
    var root = new FakeElement("#shadow-root");
    root.mode = (opts && opts.mode) || "open";
    this._shadowRoot = root;
    return root;
  }

  // 🔴 FAITHFUL TO THE REAL DOM: a CLOSED shadow root is not reachable from the
  // host. Returning it made every shadow-inspecting assertion pass for a reason
  // the browser would not reproduce. Tests reach it through `openShadow(host)`,
  // which is explicitly a test-only back door.
  get shadowRoot() {
    return this._shadowRoot && this._shadowRoot.mode === "closed"
      ? null : this._shadowRoot;
  }

  get isConnected() {
    var node = this;
    while (node.parentElement) node = node.parentElement;
    return node.tagName === "#document";
  }

  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }

  removeEventListener(type, fn) {
    var arr = this._listeners.get(type);
    if (!arr) return;
    var i = arr.indexOf(fn);
    if (i >= 0) arr.splice(i, 1);
  }

  dispatchEvent(event) {
    var arr = this._listeners.get(event.type) || [];
    for (var i = 0; i < arr.length; i++) arr[i](event);
    return !event.defaultPrevented;
  }

  querySelectorAll(selector) {
    return queryAll(this, selector);
  }

  querySelector(selector) {
    var results = queryAll(this, selector);
    return results.length > 0 ? results[0] : null;
  }

  closest(selector) {
    var node = this;
    while (node) {
      if (matchesSelector(node, selector)) return node;
      node = node.parentElement;
    }
    return null;
  }

  matches(selector) {
    return matchesSelector(this, selector);
  }
}

function parseAttrs(raw) {
  var attrs = {};
  var re = /([:\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  var m;
  while ((m = re.exec(raw))) {
    attrs[m[1].toLowerCase()] = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : "";
  }
  return attrs;
}

var VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
var RAW_TEXT = new Set(["script", "style"]);

function parseHTML(html) {
  var root = new FakeElement("#document");
  root._isConnected = true;
  var current = root;
  var i = 0;

  var push = function (node) {
    node.parentElement = current;
    current.children.push(node);
  };

  while (i < html.length) {
    var lt = html.indexOf("<", i);
    if (lt < 0) {
      current.text += html.slice(i);
      break;
    }
    if (lt > i) current.text += html.slice(i, lt);

    if (html.startsWith("<!--", lt)) {
      var end = html.indexOf("-->", lt);
      i = end < 0 ? html.length : end + 3;
      continue;
    }

    var gt = html.indexOf(">", lt);
    if (gt < 0) {
      current.text += html.slice(lt);
      break;
    }
    var inner = html.slice(lt + 1, gt).trim();

    if (inner.startsWith("/")) {
      var name = inner.slice(1).trim().toLowerCase();
      var node = current;
      while (node && node !== root && node.tagName.toLowerCase() !== name) {
        node = node.parentElement || root;
      }
      if (node && node !== root) current = node.parentElement || root;
      i = gt + 1;
      continue;
    }

    var selfClosing = inner.endsWith("/");
    var body = selfClosing ? inner.slice(0, -1) : inner;
    var space = body.search(/\s/);
    var tag = (space < 0 ? body : body.slice(0, space)).toLowerCase();
    var attrs = space < 0 ? {} : parseAttrs(body.slice(space));
    var el = new FakeElement(tag, attrs);
    push(el);
    i = gt + 1;

    if (RAW_TEXT.has(tag)) {
      var close = html.toLowerCase().indexOf("</" + tag, i);
      el.text = close < 0 ? html.slice(i) : html.slice(i, close);
      i = close < 0 ? html.length : html.indexOf(">", close) + 1;
      continue;
    }
    if (!selfClosing && !VOID.has(tag)) current = el;
  }
  return root;
}

function walk(node, visit) {
  for (var i = 0; i < node.children.length; i++) {
    visit(node.children[i]);
    walk(node.children[i], visit);
  }
}

function parseCompound(part) {
  var out = { tag: null, id: null, classes: [], attrs: [] };
  var re = /(\[[^\]]*\]|\.[\w-]+|#[\w-]+|\*|[\w-]+)/g;
  var m;
  while ((m = re.exec(part))) {
    var token = m[1];
    if (token.startsWith("[")) {
      var body = token.slice(1, -1);
      var am = body.match(/^([:\w-]+)\s*(\^=|\$=|\*=|=)?\s*(?:"([^"]*)"|'([^']*)'|(.*))?$/);
      if (am) {
        out.attrs.push({
          name: am[1].toLowerCase(),
          op: am[2] || null,
          value: am[3] !== undefined ? am[3] : am[4] !== undefined ? am[4] : am[5] === "" ? undefined : am[5],
        });
      }
    } else if (token.startsWith(".")) {
      out.classes.push(token.slice(1));
    } else if (token.startsWith("#")) {
      out.id = token.slice(1);
    } else if (token !== "*") {
      out.tag = token.toLowerCase();
    }
  }
  return out;
}

function matchesCompound(node, c) {
  if (c.tag && node.tagName.toLowerCase() !== c.tag) return false;
  if (c.id && node.getAttribute("id") !== c.id) return false;
  for (var i = 0; i < c.classes.length; i++) {
    if ((node.classList.contains ? node.classList.contains(c.classes[i]) : false) === false) return false;
  }
  for (var j = 0; j < c.attrs.length; j++) {
    var a = c.attrs[j];
    var v = node.getAttribute(a.name);
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
  var groups = selector.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
  for (var g = 0; g < groups.length; g++) {
    if (matchesSingleSelector(node, groups[g])) return true;
  }
  return false;
}

function matchesSingleSelector(node, selector) {
  var parts = selector.trim().split(/\s+/).map(parseCompound);
  var last = parts[parts.length - 1];
  if (!matchesCompound(node, last)) return false;
  var ancestor = node.parentElement;
  for (var i = parts.length - 2; i >= 0; i--) {
    var found = false;
    while (ancestor) {
      if (matchesCompound(ancestor, parts[i])) { found = true; break; }
      ancestor = ancestor.parentElement;
    }
    if (!found) return false;
    ancestor = ancestor ? ancestor.parentElement : null;
  }
  return true;
}

function queryAll(root, selector) {
  var groups = String(selector).split(",").map(function (s) { return s.trim(); }).filter(Boolean);
  var out = [];
  walk(root, function (node) {
    for (var i = 0; i < groups.length; i++) {
      if (matchesSelector(node, groups[i])) { out.push(node); return; }
    }
  });
  return out;
}

function makeDiscordDoc(html) {
  var docListeners = new Map();
  var root = parseHTML(html);
  var body = null;
  walk(root, function (node) {
    if (node.tagName.toLowerCase() === "body") body = node;
  });
  if (!body) {
    body = new FakeElement("body");
    for (var i = 0; i < root.children.length; i++) {
      body.appendChild(root.children[i]);
    }
    root.children = [body];
    body.parentElement = root;
  }
  var doc = {
    body: body,
    documentElement: body,
    querySelectorAll: function (sel) { return queryAll(root, sel); },
    querySelector: function (sel) { var r = queryAll(root, sel); return r.length > 0 ? r[0] : null; },
    getElementById: function (id) {
      var found = null;
      walk(root, function (node) {
        if (node.getAttribute("id") === id) found = node;
      });
      return found;
    },
    createElement: function (tag) {
      var el = new FakeElement(tag);
      el._ownerDocument = doc;
      return el;
    },
    // 🔴 A REAL LISTENER REGISTRY. These used to be no-ops, which made the
    // extension's production entry point — installAutoStart's document click
    // handler — structurally untestable: mutants removing the attribute check,
    // the preventDefault or the whole listener all survived a green suite.
    addEventListener: function (type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
    removeEventListener: function (type, fn) {
      var arr = docListeners.get(type);
      if (!arr) return;
      var i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
    // Bubble from `event.target` up to the document, the way a real click does,
    // so a handler that walks up from e.target is exercised for real.
    dispatchEvent: function (event) {
      var path = [];
      var n = event && event.target;
      while (n) { path.push(n); n = n.parentElement; }
      for (var p = 0; p < path.length; p++) {
        var arr = (path[p]._listeners && path[p]._listeners.get(event.type)) || [];
        for (var i = 0; i < arr.length && !event.__stopped; i++) arr[i](event);
        if (event.__stopped) return !event.defaultPrevented;
      }
      var darr = docListeners.get(event.type) || [];
      for (var j = 0; j < darr.length && !event.__stopped; j++) darr[j](event);
      return !event.defaultPrevented;
    },
    listenerCount: function (type) { return (docListeners.get(type) || []).length; },
    _root: root,
  };
  walk(root, function (node) { node._ownerDocument = doc; });
  return doc;
}

// A click that behaves like the real thing for the three properties the
// extension actually branches on. `__stopped` is what dispatchEvent above reads,
// so a handler calling stopPropagation genuinely halts the walk.
function makeClickEvent(target, opts) {
  var o = opts || {};
  var ev = {
    type: "click",
    target: target,
    button: o.button === undefined ? 0 : o.button,
    ctrlKey: !!o.ctrlKey, metaKey: !!o.metaKey,
    shiftKey: !!o.shiftKey, altKey: !!o.altKey,
    defaultPrevented: false,
    __stopped: false,
    preventDefault: function () { ev.defaultPrevented = true; },
    stopPropagation: function () { ev.__stopped = true; },
  };
  return ev;
}

// Test-only: reach a CLOSED shadow root that the host correctly hides.
function openShadow(host) {
  return host ? host._shadowRoot : null;
}

export {
  FakeElement,
  FakeComputedStyle,
  parseHTML,
  queryAll,
  matchesSelector,
  makeDiscordDoc,
  makeClickEvent,
  openShadow,
};
