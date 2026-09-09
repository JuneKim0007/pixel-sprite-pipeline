/* Application state, and the chrome that reports on it.
 *
 * `draft` is the wizard's cached form: the Back button has to return you to
 * edits you already made, so pending changes live here rather than in the DOM.
 *
 * DOM helpers live in core/dom.js and are imported, not re-exported.
 */

import { api } from './api.js';
import { $, el } from './core/dom.js';

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

// The reporter is the one function that must not throw: when it does, a working
// action looks like a dead button and the real message is never seen.
export function toast(message, kind = 'info') {
  const text = String(message ?? '');
  try {
    const host = $('#toasts') || document.body.appendChild(el('div', { id: 'toasts' }));
    const node = el('div', { className: `toast ${kind}`, textContent: text });
    host.append(node);
    setTimeout(() => node.classList.add('out'), 4200);
    setTimeout(() => node.remove(), 4800);
  } catch (e) {
    console.error(`toast could not be shown: ${text}`, e);
  }
}
