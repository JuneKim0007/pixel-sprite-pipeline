/* A DOM small enough to test against: no package.json, no build step, so no jsdom. */

class ClassList {
  constructor(node) { this.node = node; }
  get _set() {
    return new Set(String(this.node.className || '').split(/\s+/).filter(Boolean));
  }
  _write(set) { this.node.className = [...set].join(' '); }
  add(...names) { const s = this._set; names.forEach((n) => s.add(n)); this._write(s); }
  remove(...names) { const s = this._set; names.forEach((n) => s.delete(n)); this._write(s); }
  contains(name) { return this._set.has(name); }
  toggle(name, force) {
    const has = this.contains(name);
    const want = force === undefined ? !has : !!force;
    if (want) this.add(name); else this.remove(name);
    return want;
  }
}

class Node {
  constructor(tag) {
    this.tagName = String(tag || '').toUpperCase();
    this.nodeType = 1;
    this.children = [];
    this.parentNode = null;
    this.className = '';
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this._text = '';
    this._listeners = {};
    this.classList = new ClassList(this);
    // A real form control reads back '' before anything is typed.
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(this.tagName)) this.value = '';
  }

  append(...kids) {
    for (const k of kids.flat()) {
      if (k == null || k === false) continue;
      const node = k.nodeType ? k : new Text(String(k));
      node.parentNode = this;
      this.children.push(node);
    }
  }
  appendChild(k) { this.append(k); return k; }
  remove() {
    if (!this.parentNode) return;
    const i = this.parentNode.children.indexOf(this);
    if (i >= 0) this.parentNode.children.splice(i, 1);
    this.parentNode = null;
  }
  /* Present because code that splices `children` passes against an Array and throws on an HTMLCollection. */
  replaceWith(next) {
    if (!this.parentNode) return;
    const i = this.parentNode.children.indexOf(this);
    if (i >= 0) this.parentNode.children.splice(i, 1, next);
    next.parentNode = this.parentNode;
    this.parentNode = null;
  }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }

  focus() { this.focused = true; }
  getBoundingClientRect() {
    return { left: 0, top: 0, width: this.width || 0, height: this.height || 0,
             right: this.width || 0, bottom: this.height || 0 };
  }
  setPointerCapture() {}
  releasePointerCapture() {}
  /* Enough 2D context to mount a component that draws; measureText returns a width because callers lay out against it. */
  getContext() {
    if (this.tagName !== 'CANVAS') return null;
    const noop = () => {};
    return new Proxy({ measureText: (t) => ({ width: String(t).length * 7 }) }, {
      get: (t, k) => (k in t ? t[k] : noop),
      set: () => true,
    });
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }

  // The real DOM contract: descendants concatenated, and assigning replaces them.
  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) { this.children = []; this._text = String(v ?? ''); }

  /* One compound selector: '.class', 'tag', '#id', 'tag.class'. */
  _matchesOne(sel) {
    let rest = sel.trim();
    for (const m of rest.matchAll(/\[([\w-]+)=([^\]]+)\]/g)) {
      const want = m[2].replace(/^["']|["']$/g, '');
      const got = m[1] in this ? this[m[1]] : this.attributes[m[1]];
      if (String(got) !== want) return false;
    }
    rest = rest.replace(/\[[^\]]*\]/g, '');
    for (const part of rest.split(/(?=[.#])/)) {
      if (part.startsWith('.')) { if (!this.classList.contains(part.slice(1))) return false; }
      else if (part.startsWith('#')) { if (this.id !== part.slice(1)) return false; }
      else if (part && this.tagName !== part.toUpperCase()) return false;
    }
    return true;
  }

  /* Descendant combinators too, matched right to left, because assertions read that way. */
  _matches(sel) {
    const parts = sel.trim().split(/\s+/).filter(Boolean);
    if (!this._matchesOne(parts.pop())) return false;
    let node = this.parentNode;
    for (const want of parts.reverse()) {
      while (node && !(node.nodeType === 1 && node._matchesOne(want))) node = node.parentNode;
      if (!node) return false;
      node = node.parentNode;
    }
    return true;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      for (const c of n.children) {
        if (c.nodeType === 1) { if (c._matches(sel)) out.push(c); walk(c); }
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

class Text {
  constructor(v) { this.nodeType = 3; this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
}

export function installDom() {
  const doc = new Node('document');
  doc.createElement = (tag) => new Node(tag);
  doc.createTextNode = (v) => new Text(v);
  doc.body = new Node('body');
  // Appended, not only assigned: document.querySelector reaches into body in a browser.
  doc.append(doc.body);
  globalThis.document = doc;
  globalThis.window = doc;
  // `complete` stays false, so a test takes the same branch a browser does before the file loads.
  globalThis.Image = class {
    constructor() {
      this.naturalWidth = 0; this.naturalHeight = 0;
      this.complete = false; this.src = ''; this.onload = null;
    }
  };
  globalThis.Node = Node;
  return doc;
}

export { Node, Text };
