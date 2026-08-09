/* Shared state and small DOM helpers.
 *
 * `draft` is the wizard's cached form: the Back button has to return you to
 * edits you already made, so pending changes live here rather than in the DOM.
 */

import { api } from './api.js';

export const $  = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
};

export const escapeHtml = (s = '') =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const state = {
  schema: null,
  system: null,
  global: {},
  configs: [],

  current: null,      // selected pipeline name
  module: 'animation',// what that pipeline is for; scopes the settings UI
  own: {},            // what the pipeline config itself pins
  effective: {},      // global defaults merged with the above
  overrides: [],      // dotted paths the pipeline pins
  unset: [],          // resets queued for the next save
  dirty: false,

  scope: 'pipeline',  // 'pipeline' | 'global'
  settingsSection: 'Asset',

  runs: [],
  selectedRun: null,
  activeRun: null,

  // Run wizard
  wizardStep: 0,
  draft: {},          // pending edits, survives Back

  // Rig editor
  poseEntries: [],
  poseFrame: 0,
  selectedJoint: null,
  overlay: { skeleton: true, depth: false, reference: false, opacity: 0.4, refPath: null },
};

/** Merge queued edits over the effective config, without committing them. */
export function draftConfig() {
  const merged = structuredClone(state.effective || {});
  for (const [path, value] of Object.entries(state.draft)) {
    const parts = path.split('.');
    let node = merged;
    for (const key of parts.slice(0, -1)) {
      if (typeof node[key] !== 'object' || node[key] === null) node[key] = {};
      node = node[key];
    }
    node[parts.at(-1)] = value;
  }
  return merged;
}

export async function loadConfig(name) {
  const data = await api.config(name);
  state.current = name;
  // The schema is module-scoped, so switching pipeline reloads the field set
  // rather than showing knobs that do nothing for this kind of run.
  if (data.module && data.module !== state.module) {
    state.module = data.module;
    state.schema = await api.schema(data.module);
    const { JOINTS } = await import('./views.js');
    state.schema.options.joints = JOINTS;
  }
  state.own = data.config || {};
  state.effective = data.effective || {};
  state.overrides = data.overrides || [];
  state.styleRecord = data.style_record || {};
  state.unset = [];
  state.draft = {};
  state.dirty = false;
}

export function toast(message, kind = 'info') {
  const host = $('#toasts') || document.body.appendChild(el('div', { id: 'toasts' }));
  const node = el('div', { className: `toast ${kind}`, textContent: message });
  host.append(node);
  setTimeout(() => node.classList.add('out'), 4200);
  setTimeout(() => node.remove(), 4800);
}

/** Promise-based confirm with an optional "don't ask again" checkbox. */
export function confirmDialog({ title, body, confirmLabel = 'Continue', rememberKey = null }) {
  return new Promise((resolve) => {
    const remember = rememberKey ? el('label', { className: 'chk' },
      el('input', { type: 'checkbox' }), " Don't show this again") : null;

    const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });
    const ok = el('button', { className: 'btn primary', textContent: confirmLabel });

    const dialog = el('div', { className: 'modal' },
      el('div', { className: 'modal-card' },
        el('h2', { textContent: title }),
        el('div', { className: 'modal-body', innerHTML: body }),
        remember,
        el('div', { className: 'modal-actions' }, cancel, ok)));

    const close = (result) => {
      const skip = remember?.querySelector('input')?.checked;
      dialog.remove();
      resolve({ ok: result, remember: !!skip });
    };
    cancel.onclick = () => close(false);
    ok.onclick = () => close(true);
    dialog.onclick = (e) => { if (e.target === dialog) close(false); };
    document.body.append(dialog);
    ok.focus();
  });
}

export function lightbox(src, caption = '') {
  const box = el('div', { className: 'lightbox' },
    el('div', {},
      el('img', { src }),
      el('div', { className: 'cap', textContent: caption })));
  box.onclick = () => box.remove();
  document.body.append(box);
}
