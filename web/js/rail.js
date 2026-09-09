/* The workspace rail: what kind of thing you are making.
 *
 * This is the primary axis of the interface, and it is the same axis the
 * pipeline already had — `module` in pipeline/schema.py, which scopes the
 * settings fields and the style-sheet prompt templates. It was a config
 * attribute reached through a dropdown; here it is the first thing on screen,
 * because it is the first decision.
 *
 * Two properties are deliberate, and both are borrowed from PixelLab's tool
 * switcher rather than from its layout:
 *
 *   switching is never destructive   Each workspace keeps its own draft, so
 *                                    half-finished edits survive a look at
 *                                    another one. This is the whole reason
 *                                    clicking around feels safe rather than
 *                                    risky.
 *
 *   cells are named by the job       "Character sheet — one pose, several
 *                                    angles", not the name of a stage or a
 *                                    checkpoint. You pick what you want, not
 *                                    the machinery that makes it.
 *
 * Unavailable workspaces are shown, disabled. A rail with two cells is a
 * toggle; naming the ones that do not exist yet states that asset type is the
 * top-level split, and gives the next one an obvious place to land.
 */

import { el } from './core/dom.js';
import { loadConfig, state } from './store.js';

/* Which pipeline was last open in each workspace, so returning to one does not
 * dump you on an unrelated config. */
const lastConfig = {};

/* An unavailable cell names the stage it is waiting on.
 *
 * `available` is no longer a flag anyone sets. The server derives it by asking
 * whether every stage the type declares is registered, so a type turns usable
 * the day its last missing stage lands and cannot be marked ready early. Saying
 * "not built yet" threw away the only useful part of that: which work. */
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

    // A workspace with no pipeline used to be a dead end - it said which was
    // missing and refused to move. Offering to make one is what turns the rail
    // into somewhere a new kind of thing can start.
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
