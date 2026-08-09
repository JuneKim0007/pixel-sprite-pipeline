/* Style Manager: named looks, and what applying one actually does.
 *
 * A style sheet is not only prompts — it can pin a palette, a LoRA strength, a
 * sampler, and carry exemplar images, because all of those carry a look. That
 * breadth is the point, and it is also the risk: applying one can quietly
 * change a setting you had chosen deliberately.
 *
 * So the two things this view owes you are the resolved prompt (exactly what
 * the pipeline will send) and the conflicts (what the sheet overrides that
 * your pipeline also pins). Everything else is decoration.
 */

import { api } from './api.js';
import { el, state, toast } from './store.js';

function swatchStrip(colors) {
  const strip = el('div', { className: 'swatches' });
  for (const c of colors || []) {
    strip.append(el('span', { className: 'swatch', style: `background:${c}`, title: c }));
  }
  return strip;
}

function sheetCard(sheet, applied, onToggle) {
  const card = el('div', { className: `stylecard ${applied ? 'on' : ''}` });

  const toggle = el('button', {
    className: `btn ${applied ? 'primary' : 'ghost'}`,
    textContent: applied ? 'Applied' : 'Apply',
  });
  toggle.onclick = () => onToggle(sheet.name, !applied);

  card.append(el('div', { className: 'stylehead' },
    el('div', {},
      el('b', { textContent: sheet.label }),
      el('div', { className: 'path', textContent: sheet.name })),
    toggle));

  if (sheet.extends?.length) {
    card.append(el('p', { className: 'mini', textContent: `extends ${sheet.extends.join(', ')}` }));
  }
  if (sheet.notes) {
    card.append(el('p', { className: 'help', textContent: sheet.notes }));
  }

  // Vocabulary is the readable half of a sheet, so it goes above the settings.
  const vocab = el('div', { className: 'vocab' });
  for (const [group, fragments] of Object.entries(sheet.vocabulary || {})) {
    vocab.append(el('div', { className: 'vocabrow' },
      el('span', { className: 'mini', textContent: group }),
      el('span', {}, ...fragments.map((f) =>
        el('span', { className: 'frag', textContent: f })))));
  }
  if (vocab.children.length) card.append(vocab);

  const facts = [];
  if (sheet.palette) facts.push(`palette ${sheet.palette}`);
  if (sheet.token) facts.push(`token ${sheet.token}`);
  if (sheet.lora?.name) facts.push(`LoRA ${sheet.lora.name}`);
  if (sheet.exemplars?.length) facts.push(`${sheet.exemplars.length} exemplar(s)`);
  if (facts.length) {
    card.append(el('p', { className: 'mini', textContent: facts.join(' · ') }));
  }

  if (sheet.exemplars?.length) {
    const strip = el('div', { className: 'exemplars' });
    for (const path of sheet.exemplars.slice(0, 6)) {
      strip.append(el('img', { src: api.fileUrl(path), loading: 'lazy' }));
    }
    card.append(strip);
  }

  return card;
}

export function renderStyles(host, { onChanged }) {
  host.replaceChildren();
  const applied = state.effective?.styles || [];

  const toggle = async (name, on) => {
    const next = on ? [...applied, name] : applied.filter((s) => s !== name);
    try {
      await api.saveConfig(state.current, { config: { ...state.own, styles: next } });
      toast(on ? `Applied ${name}` : `Removed ${name}`);
      await onChanged?.();
      renderStyles(host, { onChanged });
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  const list = el('div', { className: 'stylelist' });
  const detail = el('div', { className: 'styledetail' });

  host.append(
    el('header', { className: 'head' },
      el('div', {},
        el('h1', { textContent: 'Styles' }),
        el('p', { className: 'sub', textContent:
          `Applied to ${state.current}: ${applied.join(' + ') || 'none'}` }))),
    el('div', { className: 'stylesbody' }, list, detail));

  (async () => {
    try {
      const { styles: sheets } = await api.styles();
      if (!sheets.length) {
        list.append(el('p', { className: 'empty', textContent:
          'No style sheets yet. Add a YAML file under styles/.' }));
        return;
      }
      for (const sheet of sheets) {
        list.append(sheetCard(sheet, applied.includes(sheet.name), toggle));
      }

      const preview = await api.stylePreview(state.current);
      detail.replaceChildren(
        el('div', { className: 'group' },
          el('h2', { textContent: 'What will be sent' }),
          el('div', { className: 'fields' },
            el('p', { className: 'resolved mono', textContent:
              preview.resolved_prompt || '(nothing resolved yet)' }),
            preview.palette
              ? el('p', { className: 'mini', textContent: `palette: ${preview.palette}` })
              : null,
            ...(preview.conflicts || []).map((c) =>
              el('p', { className: 'warnline', textContent: `⚠ ${c}` })),
            (preview.conflicts || []).length === 0
              ? el('p', { className: 'ok', textContent: '✓ No settings conflict with this pipeline.' })
              : null)));
    } catch (e) {
      list.append(el('p', { className: 'empty', textContent: e.message }));
    }
  })();
}
