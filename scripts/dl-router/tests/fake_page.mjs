// A minimal DOM stand-in for the two extension PAGES (options.html,
// picker.html, toast.html), so their `mount()` wiring can be driven headlessly.
//
// Deliberately not a DOM implementation: it supports exactly what those pages
// use -- getElementById, createElement/appendChild, textContent, value,
// checked, classList, focus, addEventListener and a query string. Anything
// richer would be a second browser to keep in sync.

class FakeEl {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.children = [];
    this._class = "";
    this.text = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.readOnly = false;
    this.focused = false;
    this.listeners = new Map();
  }

  get textContent() {
    if (this.children.length === 0) return this.text;
    return this.text + this.children.map((c) => c.textContent).join("");
  }

  set textContent(v) {
    this.text = String(v ?? "");
    this.children = [];
  }

  get className() { return this._class; }

  set className(v) { this._class = String(v ?? ""); }

  get classList() {
    const self = this;
    return {
      add(...names) {
        const cur = new Set(self._class.split(/\s+/).filter(Boolean));
        for (const n of names) cur.add(n);
        self._class = [...cur].join(" ");
      },
      remove(...names) {
        const cur = new Set(self._class.split(/\s+/).filter(Boolean));
        for (const n of names) cur.delete(n);
        self._class = [...cur].join(" ");
      },
      contains: (n) => self._class.split(/\s+/).includes(n),
    };
  }

  appendChild(child) { this.children.push(child); return child; }

  focus() { this.focused = true; }

  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }

  /** Deliver an event to every listener registered for `type`. */
  fire(type, event = {}) {
    for (const fn of this.listeners.get(type) || []) fn(event);
  }
}

/**
 * `ids` is the set of element ids the page expects to find.
 * `search` is the query string (`?id=1&dir=...`) the page parses.
 */
export function makeDoc(ids, { search = "" } = {}) {
  const els = new Map();
  for (const id of ids) els.set(id, new FakeEl());
  const docListeners = new Map();
  return {
    els,
    location: { search },
    getElementById: (id) => els.get(id) || null,
    createElement: (tag) => new FakeEl(tag),
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
    /** Deliver a document-level event (the picker listens for keydown). */
    fire(type, event = {}) {
      const e = { preventDefault() { e.defaultPrevented = true; }, ...event };
      for (const fn of docListeners.get(type) || []) fn(e);
      return e;
    },
    hasListener: (type) => (docListeners.get(type) || []).length > 0,
  };
}

export { FakeEl };
