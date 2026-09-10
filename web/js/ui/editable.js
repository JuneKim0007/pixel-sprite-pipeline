/* Authored content, shown as itself until you change it. A style's vocabulary
 * and its notes rendered as read-only chips and a <pre> while the route that
 * saves them had been serving for months. `kind` picks how the value is drawn;
 * the read/edit/save/cancel turn is the same one everywhere. */

import { el } from '../core/dom.js';
import { Button } from './kit.js';

const readers = {
  lines: (value, empty) => (value
    ? el('pre', { className: 'notes', textContent: value })
    : el('p', { className: 'empty', textContent: empty })),

  groups: (value, empty) => (Object.keys(value).length
    ? el('div', { className: 'vocab' }, ...Object.entries(value).map(([group, fragments]) =>
        el('div', { className: 'vocabrow' },
          el('span', { className: 'mini', textContent: group }),
          el('span', {}, ...fragments.map((f) =>
            el('span', { className: 'frag', textContent: f }))))))
    : el('p', { className: 'empty', textContent: empty })),
};

const editors = {
  lines(value, { rows, placeholder }) {
    const area = el('textarea', { rows, placeholder, value });
    return { node: area, read: () => area.value };
  },

  groups(value, { placeholder }) {
    const draft = Object.fromEntries(
      Object.entries(value).map(([group, fragments]) => [group, [...fragments]]));
    const box = el('div', { className: 'vocab' });

    const draw = () => {
      box.replaceChildren();
      for (const [group, fragments] of Object.entries(draft)) {
        const chips = el('span', {});
        fragments.forEach((fragment, i) => {
          const chip = el('span', { className: 'frag editable', textContent: fragment });
          const drop = el('button', { className: 'fragx', textContent: '×', title: 'Remove' });
          drop.onclick = () => { draft[group] = fragments.filter((_, j) => j !== i); draw(); };
          chip.append(drop);
          chips.append(chip);
        });
        const add = el('input', { type: 'text', className: 'fragadd', placeholder });
        add.onkeydown = (e) => {
          if (e.key !== 'Enter' || !add.value.trim()) return;
          draft[group] = [...fragments, add.value.trim()];
          draw();
        };
        chips.append(add);
        box.append(el('div', { className: 'vocabrow' },
          el('span', { className: 'mini', textContent: group }), chips));
      }

      // A sheet with no groups is otherwise a dead end: nothing to add a word to.
      const named = el('input', { type: 'text', className: 'fragadd wide', placeholder: '+ group' });
      named.onkeydown = (e) => {
        const key = named.value.trim();
        if (e.key !== 'Enter' || !key || key in draft) return;
        draft[key] = [];
        draw();
      };
      box.append(el('div', { className: 'vocabrow' }, named));
    };

    draw();
    return { node: box, read: () => draft };
  },
};

/** `onSave(next)` may return false to keep the editor open on a refusal. */
export function Editable(value, {
  kind = 'lines', rows = 8, placeholder = '', empty = 'Nothing yet.',
  locked = '', onSave,
} = {}) {
  const box = el('div', { className: `editable ${kind}` });
  let current = value;
  let editing = false;

  const draw = () => {
    box.replaceChildren();

    if (!editing) {
      const edit = Button('Edit', {
        variant: 'ghost', disabled: !!locked, title: locked,
        onClick: () => { editing = true; draw(); },
      });
      box.append(el('div', { className: 'editable-bar' }, edit),
                 readers[kind](current, empty));
      return;
    }

    const editor = editors[kind](current, { rows, placeholder });
    const save = Button('Save', { variant: 'primary' });
    const cancel = Button('Cancel', {
      variant: 'ghost', onClick: () => { editing = false; draw(); },
    });
    save.onclick = async () => {
      const next = editor.read();
      save.disabled = true;
      const ok = await onSave(next);
      save.disabled = false;
      if (ok === false) return;
      current = next;
      editing = false;
      draw();
    };
    box.append(el('div', { className: 'editable-bar' }, cancel, save), editor.node);
  };

  draw();
  return box;
}
