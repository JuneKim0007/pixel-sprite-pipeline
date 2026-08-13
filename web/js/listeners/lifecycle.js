/* Mounting a view, and unmounting the last one.
 *
 * A view returns a teardown. The mounter calls it before the next view goes
 * up, so a poll, a subscription or an animation frame cannot outlive the thing
 * that started it. Nothing here knows what any view does.
 *
 * The error handling is the error-boundary idea in a language with no
 * components: a view that throws while rendering shows the failure in place,
 * with a retry, instead of leaving a blank tab and a line in the console. That
 * was already true of boot and of nothing else.
 */

import { el } from '../core/dom.js';

let teardown = null;
let mountedName = null;

/**
 * Render `view(host)` into `host`, after tearing down whatever was there.
 *
 * `view` may return a function; it is called on the next mount. Returning
 * nothing is fine for a view with nothing to clean up.
 */
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

/** What is on screen, for tests and for the router. */
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
