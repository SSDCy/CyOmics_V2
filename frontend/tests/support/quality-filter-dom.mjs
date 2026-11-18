// Purpose-built event DOM model; not a browser or rendering engine.
export function dom() {
  const created = [];
  class Element {
    constructor(tag, ownerDocument) { this.tag = tag; this.ownerDocument = ownerDocument; this.children = []; this.parentNode = null; this.attrs = {}; this.listeners = new Map(); this.ownText = ''; this.checked = false; this.disabled = false; this.className = ''; }
    get textContent() { return this.ownText + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { for (const child of this.children) child.parentNode = null; this.children = []; this.ownText = String(value); }
    get innerHTML() { throw Error('innerHTML forbidden'); }
    set innerHTML(_) { throw Error('innerHTML forbidden'); }
    append(...nodes) { for (const node of nodes) { node.remove(); node.parentNode = this; this.children.push(node); } }
    remove() { if (this.parentNode) { this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; } }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(fn); }
    removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
    fire(type) { for (const fn of [...(this.listeners.get(type) || [])]) fn({target: this, type}); }
    click() { if (!this.disabled) this.fire('click'); }
  }
  const doc = {createElement(tag) { const node = new Element(tag, doc); created.push(node); return node; }};
  const root = doc.createElement('main');
  const all = (node = root) => [node, ...node.children.flatMap(child => all(child))];
  const by = (attribute, value) => all().find(node => node.attrs[attribute] === value);
  return {root, doc, created, all, by, listenerCount: () => created.reduce((n, node) => n + [...node.listeners.values()].reduce((a, set) => a + set.size, 0), 0)};
}
export const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
export const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
