export const ENTRIES = 30;
export const BYTES = 8 << 20;

// Bounded by both, because the two surfaces differ by orders of magnitude: a
// pose snapshot is under a kilobyte, an emphasis map is sixty-four.
export function boundedStack({ entries = ENTRIES, bytes = BYTES,
                               sizeOf = () => 0 } = {}) {
  const items = [];
  let total = 0;

  const trim = () => {
    while (items.length > entries
           || (total > bytes && items.length > 1)) {
      total -= sizeOf(items.shift());
    }
  };

  return {
    push(value) {
      items.push(value);
      total += sizeOf(value);
      trim();
    },

    pop() {
      if (!items.length) return null;
      const value = items.pop();
      total -= sizeOf(value);
      return value;
    },

    peek() {
      return items.length ? items[items.length - 1] : null;
    },

    dropWhile(match) {
      while (items.length && match(items[items.length - 1])) this.pop();
    },

    depth: () => items.length,
    bytes: () => total,

    clear() {
      items.length = 0;
      total = 0;
    },
  };
}

/** Undo and redo over any state that can be copied and written back. */
export function undoController({ read, write, sizeOf, onChange, ...caps } = {}) {
  const past = boundedStack({ sizeOf, ...caps });
  const future = boundedStack({ sizeOf, ...caps });
  let pending = null;

  const announce = () => onChange?.({
    undo: past.depth(), redo: future.depth(), bytes: past.bytes(),
  });

  const controller = {
    // A drag is one edit, not one per pointermove: begin on down, commit on up.
    begin() {
      if (pending === null) pending = read();
    },

    commit() {
      if (pending === null) return false;
      past.push(pending);
      future.clear();
      pending = null;
      announce();
      return true;
    },

    cancel() {
      pending = null;
    },

    record(change) {
      controller.begin();
      change();
      return controller.commit();
    },

    undo() {
      const previous = past.pop();
      if (previous === null) return false;
      future.push(read());
      write(previous);
      announce();
      return true;
    },

    redo() {
      const next = future.pop();
      if (next === null) return false;
      past.push(read());
      write(next);
      announce();
      return true;
    },

    canUndo: () => past.depth() > 0,
    canRedo: () => future.depth() > 0,
    depth: () => ({ undo: past.depth(), redo: future.depth() }),

    clear() {
      past.clear();
      future.clear();
      pending = null;
      announce();
    },
  };
  return controller;
}

const UNDO_KEYS = { z: 'undo', y: 'redo' };

/** Ctrl/Cmd-Z and -Y, ignored while a text field has focus. */
export function undoKeys(controller, { target = document } = {}) {
  const onKey = (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const tag = (e.target?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || e.target?.isContentEditable) return;

    const key = (e.key || '').toLowerCase();
    let action = UNDO_KEYS[key];
    if (action === 'undo' && e.shiftKey) action = 'redo';
    if (!action) return;

    e.preventDefault();
    controller[action]();
  };
  target.addEventListener('keydown', onKey);
  return () => target.removeEventListener('keydown', onKey);
}
