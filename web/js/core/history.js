import { boundedStack } from './undo.js';

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
  const past = boundedStack({ entries: limit });

  return {
    push(where) {
      if (!where || same(past.peek(), where)) return;
      past.push(where);
    },

    forget(where) {
      past.dropWhile((seen) => same(seen, where));
    },

    pop: () => past.pop(),
    canGoBack: () => past.depth() > 0,
    depth: past.depth,
    clear: past.clear,
  };
}

const shared = createHistory();
let apply = null;
let announce = null;

export function install({ read, restore, onChange }) {
  apply = { read, restore };
  announce = onChange;
  if (announce) announce(shared.canGoBack());
}

function changed() {
  if (announce) announce(shared.canGoBack());
}

export function remember() {
  if (!apply) return;
  shared.push(apply.read());
  changed();
}

export function forget() {
  if (!apply) return;
  shared.forget(apply.read());
  changed();
}

export function goBack() {
  const where = shared.pop();
  changed();
  if (!where || !apply) return false;
  apply.restore(where);
  return true;
}

export const canGoBack = () => shared.canGoBack();
