class FakeComputedStyle {
  constructor() { this._props = new Map(); }
  setProperty(prop, val, important) {
    this._props.set(String(prop).toLowerCase(), String(val) + (important ? " !important" : ""));
  }
  getPropertyValue(prop) {
    return this._props.get(String(prop).toLowerCase()) || "";
  }
  removeProperty(prop) {
    this._props.delete(String(prop).toLowerCase());
  }
}

class FakeMutationObserver {
  constructor(cb) { this._cb = cb; this._records = []; }
  observe() {}
  disconnect() {}
  takeRecords() { var r = this._records; this._records = []; return r; }
}

function matchesSelector(el, sel) {
  if (!sel || !el || !el.tagName) return false;
  sel = sel.trim();
  if (sel === "*") return true;

  var tag = null, id = null, classes = [], attrs = [];
  var rest = sel.replace(/^([a-zA-Z][a-zA-Z0-9]*)/, function (_, t) { tag = t.toLowerCase(); return ""; });
  rest = rest.replace(/#([a-zA-Z0-9_-]+)/g, function (_, i) { id = i; return ""; });
  rest = rest.replace(/\[([a-zA-Z0-9_-]+)(?:=["']?([^"'\]]+)["']?)?\]/g, function (_, name, val) {
    attrs.push({ name: name, val: val !== undefined ? val : null }); return "";
  });
  rest = rest.replace(/\.([a-zA-Z0-9_-]+)/g, function (_, c) { classes.push(c); return ""; });

  if (tag && el.tagName.toLowerCase() !== tag) return false;
  if (id && el.getAttribute("id") !== id) return false;
  for (var i = 0; i < classes.length; i++) {
    if ((" " + el.className + " ").indexOf(" " + classes[i] + " ") === -1) return false;
  }
  for (var j = 0; j < attrs.length; j++) {
    var av = el.getAttribute(attrs[j].name);
    if (attrs[j].val === null) { if (av === null) return false; }
    else if (av !== attrs[j].val) return false;
  }
  return true;
}

function parseCompoundSelector(sel) {
  var parts = sel.trim().split(/\s+/);
  return parts;
}

function matchesCompound(el, compoundSel) {
  var parts = parseCompoundSelector(compoundSel);
  if (parts.length === 0) return false;
  var node = el;
  for (var i = parts.length - 1; i >= 0; i--) {
    if (!node) return false;
    if (!matchesSelector(node, parts[i])) return false;
    node = node.parentElement;
  }
  return true;
}

class FakeElement {
  constructor(tagName, attrs, children) {
    this.tagName = tagName;
    this.attrs = attrs || {};
    this.children = [];
    this.parentElement = null;
    this.style = new FakeComputedStyle();
    this.shadowRoot = null;
    this._listeners = new Map();
    this.src = this.attrs.src || "";
    this.naturalWidth = this.attrs.naturalWidth || 0;
    this.naturalHeight = this.attrs.naturalHeight || 0;
    if (this.attrs.style) {
      var decls = this.attrs.style.split(";");
      for (var di = 0; di < decls.length; di++) {
        var parts = decls[di].split(":");
        if (parts.length >= 2) {
          var prop = parts[0].trim().toLowerCase();
          var val = parts.slice(1).join(":").trim();
          if (prop) this.style.setProperty(prop, val);
        }
      }
    }
    if (children) {
      for (var i = 0; i < children.length; i++) this.appendChild(children[i]);
    }
  }

  get id() { return this.getAttribute("id") || ""; }
  set id(v) { this.setAttribute("id", v); }

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
    if (this.children.length === 0) return this._text || "";
    return (this._text || "") + this.children.map(function (c) { return c.textContent; }).join("");
  }

  set textContent(v) { this._text = String(v ?? ""); this.children = []; }

  get className() { return this.getAttribute("class") || ""; }
  set className(v) { this.setAttribute("class", v); }

  get classList() {
    var self = this;
    return {
      add: function () {
        var cur = new Set((self.getAttribute("class") || "").split(/\s+/).filter(Boolean));
        for (var i = 0; i < arguments.length; i++) cur.add(arguments[i]);
        self.setAttribute("class", Array.from(cur).join(" "));
      },
      remove: function () {
        var cur = new Set((self.getAttribute("class") || "").split(/\s+/).filter(Boolean));
        for (var i = 0; i < arguments.length; i++) cur.delete(arguments[i]);
        self.setAttribute("class", Array.from(cur).join(" "));
      },
      contains: function (c) {
        return (" " + (self.getAttribute("class") || "") + " ").indexOf(" " + c + " ") !== -1;
      }
    };
  }

  appendChild(child) {
    this.children.push(child);
    if (child && typeof child === "object") child.parentElement = this;
    return child;
  }

  removeChild(child) {
    var i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
    if (child) child.parentElement = null;
    return child;
  }

  remove() {
    var p = this.parentElement;
    if (!p) return;
    var i = p.children.indexOf(this);
    if (i >= 0) p.children.splice(i, 1);
    this.parentElement = null;
  }

  querySelectorAll(sel) {
    var results = [];
    var compounds = sel.split(",").map(function (s) { return s.trim(); });
    this._walkDescendants(function (node) {
      for (var i = 0; i < compounds.length; i++) {
        if (matchesCompound(node, compounds[i])) { results.push(node); return; }
      }
    });
    return results;
  }

  querySelector(sel) {
    var results = this.querySelectorAll(sel);
    return results.length > 0 ? results[0] : null;
  }

  closest(sel) {
    var node = this;
    while (node) {
      if (matchesSelector(node, sel)) return node;
      node = node.parentElement;
    }
    return null;
  }

  matches(sel) { return matchesSelector(this, sel); }

  _walkDescendants(cb) {
    cb(this);
    for (var i = 0; i < this.children.length; i++) {
      this.children[i]._walkDescendants(cb);
    }
  }

  addEventListener(type, fn, opts) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push({ fn: fn, opts: opts });
  }

  removeEventListener(type, fn, opts) {
    var list = this._listeners.get(type);
    if (!list) return;
    for (var i = list.length - 1; i >= 0; i--) {
      if (list[i].fn === fn) list.splice(i, 1);
    }
  }

  dispatchEvent(evt) {
    var list = this._listeners.get(evt.type) || [];
    for (var i = 0; i < list.length; i++) list[i].fn(evt);
    return true;
  }

  attachShadow(opts) {
    var root = new FakeElement("#shadow-root");
    root.mode = (opts && opts.mode) || "open";
    this.shadowRoot = root;
    return root;
  }

  createElement(tag) { return new FakeElement(tag); }
}

function makeDiscordDoc(html) {
  var root = new FakeElement("html");
  var head = new FakeElement("head");
  var body = new FakeElement("body");
  root.appendChild(head);
  root.appendChild(body);
  var doc = new FakeElement("#document");
  doc.appendChild(root);
  doc.body = body;
  doc.head = head;
  doc.getElementById = function (id) {
    var found = null;
    root._walkDescendants(function (node) {
      if (!found && node.getAttribute && node.getAttribute("id") === id) found = node;
    });
    return found;
  };
  doc.createElement = function (tag) { return new FakeElement(tag); };
  doc.addEventListener = function () {};
  doc.removeEventListener = function () {};

  var tagRe = /<(\w+)([^>]*)>([\s\S]*?)<\/\1>/g;
  var selfClose = /<(\w+)([^>]*)\s*\/?>/g;
  var attrRe = /([\w-]+)(?:="([^"]*)")?/g;

  function parseAttrs(attrStr) {
    var attrs = {};
    var m;
    while ((m = attrRe.exec(attrStr)) !== null) {
      attrs[m[1].toLowerCase()] = m[2] !== undefined ? m[2] : "";
    }
    return attrs;
  }

  function buildChildren(parent, innerHtml) {
    var childHtml = innerHtml.replace(/<!--[\s\S]*?-->/g, "");
    var re = /<(\w+)([^>]*)>([\s\S]*?)<\/\1>|<(\w+)([^>]*)\s*\/?>/g;
    var m;
    while ((m = re.exec(childHtml)) !== null) {
      var tag = m[1] || m[4];
      var attrs = parseAttrs(m[2] || m[5]);
      var inner = m[3] || "";
      var el = new FakeElement(tag, attrs);
      if (inner && tag !== "img" && tag !== "video" && tag !== "source") {
        buildChildren(el, inner);
      }
      parent.appendChild(el);
    }
  }

  var bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  if (bodyMatch) {
    buildChildren(body, bodyMatch[1]);
  } else {
    buildChildren(body, html);
  }
  return doc;
}

export { FakeElement, FakeComputedStyle, FakeMutationObserver, makeDiscordDoc };
