/* Application state, and the chrome that reports on it.
 *
 * `draft` is the wizard's cached form: the Back button has to return you to
 * edits you already made, so pending changes live here rather than in the DOM.
 *
 * DOM helpers moved to core/dom.js. They are re-exported here so no call site
 * had to change in the commit that moved them.
 */

import { api } from './api.js';
import { el } from './core/dom.js';

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

  // Pending edits, per workspace.
  //
  // One shared draft was fine while there was one kind of thing to make. With
  // the module as the primary axis it is not: switching from a character sheet
  // to a tileset would carry the sheet's half-finished edits into a form that
  // has no field for them, and switching back would find them gone. Keyed by
  // module, switching is never destructive - which is the one load-bearing
  // decision in PixelLab's tool switcher, and the reason it feels safe to
  // click around in.
  drafts: {},

  // Rig editor
  poseEntries: [],
  poseFrame: 0,
  selectedJoint: null,
  overlay: { skeleton: true, depth: false, reference: false, opacity: 0.4, refPath: null },
};

/** The pending edits for the workspace in front of you. */
export function draft() {
  const key = state.module || 'animation';
  state.drafts[key] = state.drafts[key] || {};
  return state.drafts[key];
}

export function clearDraft() {
  state.drafts[state.module || 'animation'] = {};
}

/** Merge queued edits over the effective config, without committing them. */
export function draftConfig() {
  const merged = structuredClone(state.effective || {});
  for (const [path, value] of Object.entries(draft())) {
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
    const { JOINTS } = await import('./features/pose.js');
    state.schema.options.joints = JOINTS;
  }
  state.own = data.config || {};
  state.effective = data.effective || {};
  state.overrides = data.overrides || [];
  state.styleRecord = data.style_record || {};
  state.unset = [];
  // Deliberately does NOT clear the draft: loading a config for the workspace
  // you are already in should not silently discard edits you have made in it.
  // Committing or starting a run clears it; that is where it belongs.
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
