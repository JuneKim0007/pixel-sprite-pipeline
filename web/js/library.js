/* Making the things that live in `library/` — a pipeline config, an asset type.
 *
 * A type may require a stage nothing registers; that is how `tileset` says what
 * it is waiting for, so the extra box is not a mistake to prevent. An order that
 * could never run is prevented, and only when every stage in it exists, because
 * an order naming an unknown stage cannot be checked at all. */

import { api } from './api.js';
import { autoOrder, orderProblems } from './fields.js';
import { configsFor } from './rail.js';
import { el } from './core/dom.js';
import { dialog, field } from './ui/dialog.js';
import { state, toast } from './store.js';

const NAME_RULE = /^[A-Za-z0-9_-]+$/;

export function starterConfig(name, module, stages) {
  return { name, module, pipeline: { stages } };
}

export function newPipelineDialog(module, meta) {
  return dialog({ title: `New ${(meta?.label || module).toLowerCase()} pipeline` },
    (close) => {
      const taken = new Set((state.configs || []).map((c) => c.name));
      const name = el('input', { type: 'text', className: 'wide', placeholder: `my_${module}` });
      const from = el('select', { className: 'select' },
        el('option', { value: '', textContent: 'Blank — every stage, in dependency order' }));
      for (const c of configsFor(module)) {
        from.append(el('option', { value: c, textContent: `Copy of ${c}` }));
      }

      const why = el('p', { className: 'help' });
      const create = el('button', { className: 'btn primary', textContent: 'Create' });
      const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });
      cancel.onclick = () => close(null);

      const check = () => {
        const v = name.value.trim();
        why.textContent = !v ? ''
          : !NAME_RULE.test(v) ? 'Letters, digits, dash and underscore only.'
          : taken.has(v) ? `'${v}' already exists.` : '';
        create.disabled = !v || !!why.textContent;
      };
      name.addEventListener('input', check);
      check();

      create.onclick = async () => {
        const chosen = name.value.trim();
        create.disabled = true;
        try {
          const body = from.value
            ? { ...((await api.config(from.value)).config || {}), name: chosen, module }
            : starterConfig(chosen, module,
                            autoOrder(state.schema.stages.map((s) => s.name)));
          await api.saveConfig(chosen, { config: body });
          toast(`Created ${chosen}`);
          close(chosen);
        } catch (e) {
          why.textContent = e.message;
          create.disabled = false;
        }
      };

      return {
        content: [
          field('Name', name, 'library/configs/<name>.yaml', why),
          field('Start from', from, null,
                el('p', { className: 'help', textContent:
                  'Settings → Pipeline can reorder the stages afterwards; the '
                  + 'order is validated before any run.' })),
        ],
        actions: [cancel, create],
        opened: () => name.focus(),
      };
    });
}


const KEY_RULE = /^[a-z][a-z0-9_]*$/;

export function newTypeDialog() {
  return dialog({ title: 'New asset type', wide: true }, (close) => {
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
    cancel.onclick = () => close(null);

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

    const hint = (text) => el('p', { className: 'help', textContent: text });
    return {
      content: [
        field('Key', key, 'library/modules/<key>.yaml',
              hint('What a config names in `module:`.')),
        field('Name', label, null, hint('Shown on the rail cell.')),
        field('One line', detail, null, hint('The cell subtitle — what this makes, not how.')),
        field('Description', blurb, null, hint('Shown on hover. Say what makes this different from the others.')),
        field('Also show settings from', extends_, null,
              hint('A new type otherwise shows only the settings no type claims, which reads as the form having lost half its knobs.')),
        field('Attach props', props, null,
              hint('Off for a reference sheet: a weapon occludes the torso it crosses.')),
        field('Stages', stageBox, null,
              hint('The order a new pipeline of this type starts from.')),
        field('Stages that do not exist yet', extra, null,
              hint('Comma separated. The type is saved and stays unavailable until something registers them — this is how a type states the work it is waiting on.')),
        el('div', { className: 'field' },
          el('label', { textContent: 'Resulting order' }), order, why),
      ],
      actions: [cancel, create],
      opened: () => { recheck(); key.focus(); },
    };
  });
}
