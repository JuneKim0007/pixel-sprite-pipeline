/* A view declares what it reads; set() notifies whoever reads it.
 * subscribe returns its own unsubscribe, so a listener cannot outlive its view. */
const listeners = new Map();
let nextId = 0;

/** Run fn when any of keys is set. Returns the unsubscribe. */
export function subscribe(keys, fn) {
  const id = nextId++;
  listeners.set(id, { keys: new Set([keys].flat()), fn });
  return () => listeners.delete(id);
}

/* One call per listener however many of its keys changed.
 * A throwing listener must not take the others with it. */
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

/** Live listener count, for the teardown test. */
export function listenerCount() {
  return listeners.size;
}
