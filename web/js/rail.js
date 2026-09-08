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
import { autoOrder, orderProblems } from './fields.js';
import { el } from './core/dom.js';
import { loadConfig, state, toast } from './store.js';

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

export function renderRail(host, { onSwitch, onCreated, onTypeCreated }) {
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
  strip.append(newTypeCell(async () => {
    const made = await newTypeDialog();
    if (made) await onTypeCreated?.(made);
  }));
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

/* Defining an asset type, which until now meant editing a dict in schema.py.
 *
 * The stage list is the interesting part. A type may require a stage nothing
 * registers — that is how `tileset` says what it is waiting for — so the extra
 * box is not a mistake to prevent. What IS prevented is an order that could
 * never run, and only when every stage in it exists, because an order
 * containing an unknown stage cannot be checked at all. */

const KEY_RULE = /^[a-z][a-z0-9_]*$/;

export function newTypeDialog() {
  return new Promise((resolve) => {
    const known = (state.schema?.stages || []).map((s) => s.name);
    const taken = new Set(Object.keys(state.schema?.modules || {}));
    const chosen = new Set(known.filter((n) => n !== 'softbody'));

    const key = el('input', { type: 'text', className: 'wide', placeholder: 'portrait' });
    const label = el('input', { type: 'text', className: 'wide', placeholder: 'Portraits' });
    const detail = el('input', { type: 'text', className: 'wide', placeholder: 'one face, several expressions' });
    const blurb = el('textarea', { rows: 2, className: 'wide', placeholder: 'What this kind of asset is, and what makes it different.' });
    const extra = el('input', { type: 'text', className: 'wide', placeholder: 'tile_edges, grid_fit' });
    const props = el('input', { type: 'checkbox' });

    const extends_ = el('select', { className: 'select' },
      el('option', { value: '', textContent: 'Nothing — only the shared settings' }));
    for (const [k, m] of Object.entries(state.schema?.modules || {})) {
      extends_.append(el('option', { value: k, textContent: `${m.label} — also show its settings` }));
    }

    const stageBox = el('div', { className: 'stagepick' });
    const order = el('p', { className: 'help mono' });
    const why = el('p', { className: 'help' });
    const create = el('button', { className: 'btn primary', textContent: 'Create type' });
    const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });

    const wanted = () => {
      const typed = extra.value.split(',').map((s) => s.trim()).filter(Boolean);
      return [...autoOrder([...chosen]), ...typed.filter((t) => !chosen.has(t))];
    };

    const recheck = () => {
      const stages = wanted();
      const missing = stages.filter((s) => !known.includes(s));
      const problems = missing.length ? [] : orderProblems(stages);
      order.textContent = stages.join(' → ') || '(no stages)';
      const k = key.value.trim();
      why.textContent =
        !k ? ''
        : !KEY_RULE.test(k) ? 'Lowercase letters, digits and underscore; must start with a letter.'
        : taken.has(k) ? `'${k}' already exists.`
        : problems.length ? problems[0]
        : missing.length ? `Will stay unavailable until ${missing.join(', ')} exist.`
        : '';
      create.disabled = !k || !label.value.trim() || !detail.value.trim()
        || !blurb.value.trim() || !stages.length
        || !KEY_RULE.test(k) || taken.has(k) || problems.length > 0;
    };

    for (const name of known) {
      const box = el('input', { type: 'checkbox', checked: chosen.has(name) });
      box.onchange = () => { box.checked ? chosen.add(name) : chosen.delete(name); recheck(); };
      stageBox.append(el('label', { className: 'chk' }, box, ` ${name}`));
    }
    for (const input of [key, label, detail, blurb, extra]) {
      input.addEventListener('input', recheck);
    }

    const row = (title, control, hint) => el('div', { className: 'field' },
      el('div', { className: 'field-top stacked' },
        el('div', {}, el('label', { textContent: title })),
        el('div', { className: 'control-wrap' }, control)),
      hint ? el('p', { className: 'help', textContent: hint }) : null);

    const dialog = el('div', { className: 'modal' },
      el('div', { className: 'modal-card wide' },
        el('h2', { textContent: 'New asset type' }),
        el('div', { className: 'fields' },
          row('Key', key, 'The filename in library/modules/, and what a config names in `module:`.'),
          row('Name', label, 'Shown on the rail cell.'),
          row('One line', detail, 'The cell subtitle — what this makes, not how.'),
          row('Description', blurb, 'Shown on hover. Say what makes this different from the others.'),
          row('Also show settings from', extends_,
              'A new type otherwise shows only the settings no type claims, which reads as the form having lost half its knobs.'),
          row('Attach props', props, 'Off for a reference sheet: a weapon occludes the torso it crosses.'),
          row('Stages', stageBox, 'The order a new pipeline of this type starts from.'),
          row('Stages that do not exist yet', extra,
              'Comma separated. The type is saved and stays unavailable until something registers them — this is how a type states the work it is waiting on.'),
          el('div', { className: 'field' }, el('label', { textContent: 'Resulting order' }), order, why)),
        el('div', { className: 'modal-actions' }, cancel, create)));

    const close = (v) => { dialog.remove(); resolve(v); };
    cancel.onclick = () => close(null);
    dialog.onclick = (e) => { if (e.target === dialog) close(null); };

    create.onclick = async () => {
      const name = key.value.trim();
      create.disabled = true;
      try {
        await api.saveModule(name, {
          label: label.value.trim(), detail: detail.value.trim(),
          blurb: blurb.value.trim(), extends: extends_.value,
          props: props.checked, stages: wanted(),
        });
        toast(`Created ${label.value.trim()}`);
        close(name);
      } catch (e) {
        why.textContent = e.message;
        create.disabled = false;
      }
    };

    document.body.append(dialog);
    recheck();
    key.focus();
  });
}
