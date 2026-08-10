/* A DOM small enough to test against, with no dependencies.
 *
 * The front-end has no package.json and no build step, which is deliberate —
 * so jsdom is not available and adding it would buy one test harness at the
 * cost of the property that makes this UI easy to run anywhere.
 *
 * What the UI actually touches is narrow: createElement, append, textContent,
 * className/classList, dataset, and a class/tag/id query. That is implementable
 * in a page of plain JS, and it is enough to render a component and assert what
 * came out — which is the thing a refactor needs and static checks cannot give.
 *
 *   import { installDom } from './domshim.mjs';
 *   installDom();            // defines globalThis.document
 */

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
  replaceChildren(...kids) { this.children = []; this.append(...kids); }

  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }

  /* Text is the concatenation of descendants, and assigning it replaces them —
   * the same contract the real DOM has, because components rely on both. */
  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) { this.children = []; this._text = String(v ?? ''); }

  /* One compound selector: '.class', 'tag', '#id', 'tag.class'. */
  _matchesOne(sel) {
    for (const part of sel.trim().split(/(?=[.#])/)) {
      if (part.startsWith('.')) { if (!this.classList.contains(part.slice(1))) return false; }
      else if (part.startsWith('#')) { if (this.id !== part.slice(1)) return false; }
      else if (part && this.tagName !== part.toUpperCase()) return false;
    }
    return true;
  }

  /* Descendant combinators too ('.a .b'), because assertions naturally read
   * that way — "the tip inside the label row" — and a shim that silently
   * fails to match makes a passing component look broken. Matched right to
   * left: the last compound must match this node, and each earlier one must
   * match some ancestor, in order. */
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
  globalThis.document = doc;
  globalThis.Node = Node;
  return doc;
}

export { Node, Text };
