/* mount() tears down the previous view before the next renders.
 * A view that throws shows its error in place instead of blanking the tab. */
import { el } from '../core/dom.js';

let teardown = null;
let mountedName = null;

/** Render view(host), first tearing down the last. A returned function is
 *  called on the next mount. */
export function mount(name, host, view) {
  unmount();
  mountedName = name;
  try {
    const cleanup = view(host);
    teardown = typeof cleanup === 'function' ? cleanup : null;
  } catch (e) {
    teardown = null;
    host.replaceChildren(failure(name, e, () => mount(name, host, view)));
    console.error(`view '${name}' failed to render:`, e);
  }
}

export function unmount() {
  if (!teardown) { mountedName = null; return; }
  const fn = teardown;
  teardown = null;
  mountedName = null;
  try {
    fn();
  } catch (e) {
    console.error('teardown failed:', e);
  }
}

/** What is on screen. */
export function mounted() {
  return mountedName;
}

/** Collect several teardowns into the one a view returns. */
export function teardowns(...fns) {
  const list = fns.filter(Boolean);
  return () => { for (const fn of list) fn(); };
}

function failure(name, error, retry) {
  const again = el('button', { className: 'btn', textContent: 'Try again', type: 'button' });
  again.onclick = retry;
  return el('div', { className: 'viewerror' },
    el('h2', { textContent: `The ${name} tab could not be drawn` }),
    el('pre', { className: 'joberror', textContent: `${error.name}: ${error.message}` }),
    again);
}
