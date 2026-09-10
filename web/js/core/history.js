export const COORDS = [
  'tab', 'settingsSection', 'scope', 'wizardStep', 'selectedRun', 'module',
];

export const LIMIT = 30;

export function snapshot(state) {
  const out = {};
  for (const key of COORDS) out[key] = state[key];
  return out;
}

export function same(a, b) {
  if (!a || !b) return false;
  return COORDS.every((key) => a[key] === b[key]);
}

export function createHistory({ limit = LIMIT } = {}) {
  const past = [];

  return {
    push(where) {
      if (!where) return;
      if (same(past[past.length - 1], where)) return;
      past.push(where);
      if (past.length > limit) past.shift();
    },

    forget(where) {
      while (past.length && same(past[past.length - 1], where)) past.pop();
    },

    pop() {
      return past.pop() || null;
    },

    canGoBack() {
      return past.length > 0;
    },

    depth() {
      return past.length;
    },

    clear() {
      past.length = 0;
    },
  };
}
