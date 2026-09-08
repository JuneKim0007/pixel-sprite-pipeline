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
import { autoOrder } from './fields.js';
import { el } from './core/dom.js';
import { loadConfig, state, toast } from './store.js';

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

/** The names of the configs belonging to one workspace. */
export function configsFor(module) {
  return (state.configs || []).filter((c) => c.module === module).map((c) => c.name);
}

export function renderRail(host, { onSwitch, onCreated }) {
  const modules = state.schema?.modules || {};
  host.replaceChildren();

  const pick = async (key) => {
    if (key === state.module) return;
    lastConfig[state.module] = state.current;

    // A workspace with no pipeline used to be a dead end - it said which was
    // missing and refused to move. Offering to make one is what turns the rail
    // into somewhere a new kind of thing can start.
    const mine = configsFor(key);
    const target = lastConfig[key] || mine[0];
    if (!target) {
      const made = await newPipelineDialog(key, modules[key]);
      if (!made) return;
      await onCreated?.(made);
    } else {
      await loadConfig(target);
    }
    onSwitch?.(key);
  };

  const strip = el('div', { className: 'rail' });
  for (const [key, meta] of Object.entries(modules)) {
    strip.append(cell(key, meta, { active: key === state.module, onPick: pick }));
  }
  host.append(strip);
}

/* Creating a pipeline, which the UI could not do at all.
 *
 * `PUT /api/config` already writes a new file when the target is absent, so
 * this is a form over a route that existed. Blank starts from every registered
 * stage in dependency order; copying takes another pipeline's own keys and
 * re-points the name and the workspace. */

const NAME_RULE = /^[A-Za-z0-9_-]+$/;

export function starterConfig(name, module, stages) {
  return { name, module, pipeline: { stages } };
}

export function newPipelineDialog(module, meta) {
  return new Promise((resolve) => {
    const taken = new Set((state.configs || []).map((c) => c.name));
    const siblings = configsFor(module);

    const name = el('input', { type: 'text', className: 'wide', placeholder: `my_${module}` });
    const from = el('select', { className: 'select' },
      el('option', { value: '', textContent: 'Blank — every stage, in dependency order' }));
    for (const c of siblings) from.append(el('option', { value: c, textContent: `Copy of ${c}` }));

    const why = el('p', { className: 'help' });
    const create = el('button', { className: 'btn primary', textContent: 'Create' });
    const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });

    const check = () => {
      const v = name.value.trim();
      why.textContent = !v ? ''
        : !NAME_RULE.test(v) ? 'Letters, digits, dash and underscore only.'
        : taken.has(v) ? `'${v}' already exists.` : '';
      create.disabled = !v || !!why.textContent;
    };
    name.addEventListener('input', check);
    check();

    const dialog = el('div', { className: 'modal' },
      el('div', { className: 'modal-card' },
        el('h2', { textContent: `New ${(meta?.label || module).toLowerCase()} pipeline` }),
        el('div', { className: 'fields' },
          el('div', { className: 'field' },
            el('div', { className: 'field-top stacked' },
              el('div', {}, el('label', { textContent: 'Name' }),
                el('div', { className: 'path', textContent: 'library/configs/<name>.yaml' })),
              el('div', { className: 'control-wrap' }, name)),
            why),
          el('div', { className: 'field' },
            el('div', { className: 'field-top stacked' },
              el('div', {}, el('label', { textContent: 'Start from' })),
              el('div', { className: 'control-wrap' }, from)),
            el('p', { className: 'help', textContent:
              'Settings → Pipeline can reorder the stages afterwards; the order is validated before any run.' }))),
        el('div', { className: 'modal-actions' }, cancel, create)));

    const close = (value) => { dialog.remove(); resolve(value); };
    cancel.onclick = () => close(null);
    dialog.onclick = (e) => { if (e.target === dialog) close(null); };

    create.onclick = async () => {
      const chosen = name.value.trim();
      create.disabled = true;
      try {
        let body;
        if (from.value) {
          const source = await api.config(from.value);
          body = { ...(source.config || {}), name: chosen, module };
        } else {
          body = starterConfig(chosen, module,
                               autoOrder(state.schema.stages.map((s) => s.name)));
        }
        await api.saveConfig(chosen, { config: body });
        toast(`Created ${chosen}`);
        close(chosen);
      } catch (e) {
        why.textContent = e.message;
        create.disabled = false;
      }
    };

    document.body.append(dialog);
    name.focus();
  });
}
