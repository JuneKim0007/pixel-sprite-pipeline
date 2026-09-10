// Application state, and the chrome that reports on it.

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
  state.dirty = false;
}

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
