/* Who cares when what changes.
 *
 * Today a view mutates `state` and then remembers to call the right render
 * function. Six of the ten views define a `rerender` that calls their own
 * top-level render, so a checkbox toggling destroys and rebuilds the whole
 * panel - which is why the editor loses scroll and focus, and why fourteen
 * module-level `let`s exist to hold state that a rerender would otherwise wipe.
 *
 * This is React's Context-and-reducer idea with the React removed. A view
 * declares what it reads; `set` notifies whoever reads it. Nothing polls,
 * nothing diffs a tree, and nobody has to remember which render to call.
 *
 *     const stop = subscribe(['runs'], () => redrawList());
 *     set('runs', await api.runs());        // redrawList happens
 *     stop();                               // and stops happening
 *
 * `subscribe` returns its own unsubscribe, which is the whole teardown story:
 * a view hands that function to the lifecycle and cannot leak a listener
 * without also failing to clean up, so the two cannot drift apart.
 *
 * Keys are top-level names rather than dotted paths. A dotted path invites
 * subscribing to `runs.0.stages.2`, and anything watching that closely wants
 * to be looking at a value it was handed instead.
 */

const listeners = new Map();
let nextId = 0;

/** Run `fn` whenever any of `keys` is set. Returns the unsubscribe. */
export function subscribe(keys, fn) {
  const id = nextId++;
  listeners.set(id, { keys: new Set([keys].flat()), fn });
  return () => listeners.delete(id);
}

/* Notify, once per listener, even when several of its keys changed together.
 *
 * A failing listener does not stop the others. One view throwing should not
 * take the rest of the page with it, and it should be visible rather than
 * silent - the same reason a layer that raises in the editor reports itself
 * instead of blanking the preview.
 */
export function notify(...keys) {
  const touched = new Set(keys.flat());
  for (const { keys: watched, fn } of [...listeners.values()]) {
    if (![...touched].some((k) => watched.has(k))) continue;
    try {
      fn(touched);
    } catch (e) {
      console.error('subscriber failed:', e);
    }
  }
}

/** Assign and notify. The only supported way to change watched state. */
export function setOn(target, key, value) {
  target[key] = value;
  notify(key);
  return value;
}

/** How many listeners are live, for the test that asserts teardown works. */
export function listenerCount() {
  return listeners.size;
}
