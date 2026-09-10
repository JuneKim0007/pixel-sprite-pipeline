// The workspace rail: what kind of thing you are making.

import { el } from './core/dom.js';
import { loadConfig, state } from './store.js';

// Which pipeline was last open in each workspace.
const lastConfig = {};

// An unavailable cell names the stage it is waiting on.
function cell(key, meta, { active, onPick }) {
  const waiting = (meta.missing || []).join(', ');
  const btn = el('button', {
    className: `rail-cell ${active ? 'on' : ''} ${meta.available ? '' : 'soon'}`,
    type: 'button',
    disabled: !meta.available,
    title: meta.available ? meta.blurb
      : `${meta.blurb}\n\nNeeds ${waiting || 'work that does not exist yet'}.`,
  },
    el('span', { className: 'rail-label', textContent: meta.label }),
    el('span', { className: 'rail-detail',
                 textContent: meta.available ? meta.detail
                   : waiting ? `needs ${waiting}` : 'not built yet' }));
  if (meta.available) btn.onclick = () => onPick(key);
  return btn;
}

function newTypeCell(onPick) {
  const btn = el('button', {
    className: 'rail-cell add', type: 'button',
    title: 'Define a new kind of asset. It may name stages that do not exist '
         + 'yet; it stays unavailable until they are registered.',
  },
    el('span', { className: 'rail-label', textContent: '+ New type' }),
    el('span', { className: 'rail-detail', textContent: 'your own workspace' }));
  btn.onclick = onPick;
  return btn;
}

/** The names of the configs belonging to one workspace. */
export function configsFor(module) {
  return (state.configs || []).filter((c) => c.module === module).map((c) => c.name);
}

export function renderRail(host, { onSwitch, onEmpty, onNewType }) {
  const modules = state.schema?.modules || {};
  host.replaceChildren();

  const pick = async (key) => {
    if (key === state.module) return;
    lastConfig[state.module] = state.current;

    const target = lastConfig[key] || configsFor(key)[0];
    if (!target) {
      if (!await onEmpty?.(key, modules[key])) return;
    } else {
      await loadConfig(target);
    }
    onSwitch?.(key);
  };

  const strip = el('div', { className: 'rail' });
  for (const [key, meta] of Object.entries(modules)) {
    strip.append(cell(key, meta, { active: key === state.module, onPick: pick }));
  }
  strip.append(newTypeCell(() => onNewType?.()));
  host.append(strip);
}
