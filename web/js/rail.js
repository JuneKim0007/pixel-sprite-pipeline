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

import { api } from './api.js';
import { el, loadConfig, state, toast } from './store.js';

/* Which pipeline was last open in each workspace, so returning to one does not
 * dump you on an unrelated config. */
const lastConfig = {};

function cell(key, meta, { active, onPick }) {
  const btn = el('button', {
    className: `rail-cell ${active ? 'on' : ''} ${meta.available ? '' : 'soon'}`,
    type: 'button',
    disabled: !meta.available,
    title: meta.available ? meta.blurb : `${meta.blurb}\n\nNot built yet.`,
  },
    el('span', { className: 'rail-label', textContent: meta.label }),
    el('span', { className: 'rail-detail',
                 textContent: meta.available ? meta.detail : 'not built yet' }));
  if (meta.available) btn.onclick = () => onPick(key);
  return btn;
}

/** Configs belonging to one workspace, by the `module` each declares. */
export function configsFor(module) {
  return (state.configs || []).filter((c) =>
    (typeof c === 'string' ? state.configModules?.[c] : c.module) === module);
}

export function renderRail(host, { onSwitch }) {
  const modules = state.schema?.modules || {};
  host.replaceChildren();

  const pick = async (key) => {
    if (key === state.module) return;
    lastConfig[state.module] = state.current;

    // A workspace is only usable if a pipeline exists for it. Say which is
    // missing rather than switching to an empty screen.
    const mine = configsFor(key);
    const target = lastConfig[key] || mine[0];
    if (!target) {
      toast(`No pipeline is set to ${modules[key].label.toLowerCase()} yet`, 'error');
      return;
    }
    await loadConfig(target);
    onSwitch?.(key);
  };

  const strip = el('div', { className: 'rail' });
  for (const [key, meta] of Object.entries(modules)) {
    strip.append(cell(key, meta, { active: key === state.module, onPick: pick }));
  }
  host.append(strip);
}

/** The module each config declares, so the rail can filter without a fetch. */
export async function indexConfigModules() {
  const out = {};
  await Promise.all((state.configs || []).map(async (name) => {
    try {
      const data = await api.config(name);
      out[name] = data.module || 'animation';
    } catch {
      out[name] = 'animation';
    }
  }));
  state.configModules = out;
  return out;
}
