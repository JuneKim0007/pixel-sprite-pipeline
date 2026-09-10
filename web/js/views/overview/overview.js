// Overview: the handful of things actually done every session, on one screen.

import { api } from '../../api.js';
import { showError } from '../../core/errors.js';
import { el } from '../../core/dom.js';
import { Button, Empty, PanelHead } from '../../ui/index.js';
import { state, toast } from '../../store.js';

function card(title, { action, onAction } = {}) {
  let button = null;
  if (action) {
    button = Button(action, { variant: 'ghost' });
    button.onclick = onAction;
  }
  return el('section', { className: 'ovcard' }, PanelHead(title, { action: button }));
}

function statLine(pairs) {
  return el('div', { className: 'ovstats' },
    ...pairs.filter(Boolean).map(([k, v, tone]) =>
      el('div', { className: `ovstat ${tone || ''}` },
        el('b', { textContent: String(v) }),
        el('span', { className: 'mini', textContent: k }))));
}

/* ------------------------------------------------------- style context */

function contextStrip(detail, refresh) {
  const box = el('div', {});
  if (!detail) {
    return Empty('No style applied to this pipeline.');
  }

  const images = detail.context.images || [];
  const grid = el('div', { className: 'ctxgrid tight' });

  for (const image of images) {
    const cell = el('figure', { className: `ctxcell ${image.missing ? 'gone' : ''}` });
    cell.append(image.missing
      ? el('div', { className: 'ctxmissing', textContent: '⚠' })
      : el('img', { src: api.fileUrl(image.path), loading: 'lazy', alt: image.name }));

    const drop = el('button', { className: 'cellx', textContent: '✕',
                                title: 'Remove from this style' });
    drop.onclick = async () => {
      try {
        await api.styleExemplar(detail.name, [image.path], true);
        toast(`Removed ${image.name}`);
        refresh();
      } catch (e) { showError(e); }
    };
    cell.append(drop);
    cell.append(el('figcaption', {},
      el('span', { className: 'name', textContent: image.name, title: image.path })));
    grid.append(cell);
  }

  const upload = el('input', { type: 'file', accept: 'image/*', multiple: true,
                               style: 'display:none' });
  upload.onchange = async () => {
    if (!upload.files.length) return;
    try {
      const { saved } = await api.upload(upload.files);
      await api.styleExemplar(detail.name, saved.map((f) => f.path));
      toast(`Added ${saved.length} exemplar(s) to ${detail.name}`);
      refresh();
    } catch (e) { showError(e); }
    upload.value = '';
  };

  const add = el('button', { className: 'ctxadd', textContent: '+',
                             title: 'Add a style exemplar' });
  add.onclick = () => upload.click();
  grid.append(el('figure', { className: 'ctxcell addcell' }, add,
    el('figcaption', {}, el('span', { className: 'mini', textContent: 'add' }))));

  box.append(grid, upload);
  if (!detail.foldered) {
    box.append(el('p', { className: 'warnline', textContent:
      `⚠ ${detail.name} is a single YAML file, so it cannot hold exemplars. `
      + `Move it to styles/${detail.name}/style.yaml.` }));
  }
  return box;
}

function promptStrip(detail, refresh) {
  if (!detail) return null;
  const vocab = { ...(detail.context.prompts.vocabulary || {}) };
  const box = el('div', { className: 'promptedit' });

  const save = async () => {
    try {
      await api.stylePrompts(detail.name, vocab, null);
      toast('Vocabulary saved');
      refresh();
    } catch (e) { showError(e); }
  };

  for (const [group, fragments] of Object.entries(vocab)) {
    const row = el('div', { className: 'vocabrow' },
      el('span', { className: 'mini', textContent: group }));
    const chips = el('span', {});
    fragments.forEach((fragment, i) => {
      const chip = el('span', { className: 'frag editable', textContent: fragment });
      const x = el('button', { className: 'fragx', textContent: '×', title: 'Remove' });
      x.onclick = () => { vocab[group] = fragments.filter((_, j) => j !== i); save(); };
      chip.append(x);
      chips.append(chip);
    });

    const input = el('input', { type: 'text', className: 'fragadd', placeholder: '+ add' });
    input.onkeydown = (e) => {
      if (e.key !== 'Enter' || !input.value.trim()) return;
      vocab[group] = [...fragments, input.value.trim()];
      save();
    };
    chips.append(input);
    row.append(chips);
    box.append(row);
  }

  if (!Object.keys(vocab).length) {
    box.append(Empty('No vocabulary groups.'));
  }

  return box;
}

/* ------------------------------------------------------------------ view */

export function renderOverview(host, { goTo }) {
  host.replaceChildren();

  const applied = state.effective?.styles || [];
  const stages = state.effective?.pipeline?.stages || [];

  host.append(el('header', { className: 'head' },
    el('div', {},
      el('h1', { textContent: state.current || 'Pipeline' }),
      el('p', { className: 'sub', textContent:
        `${state.effective?.subject || 'no subject set'} · `
        + `${stages.length} stage(s) · ${applied.join(' + ') || 'no style'}` })),
    el('div', { className: 'head-actions' },
      (() => {
        const b = Button('Set up a run', { variant: 'primary' });
        b.onclick = () => goTo('run');
        return b;
      })())));

  const grid = el('div', { className: 'ovgrid' });
  host.append(grid);

  /* -- style context: the most frequent edit, so it comes first -------- */
  const styleCard = card(`Style context${applied.length ? ` · ${applied.at(-1)}` : ''}`, {
    action: 'Manage styles', onAction: () => goTo('styles'),
  });
  styleCard.append(el('p', { className: 'ovloading', textContent: 'loading…' }));
  grid.append(styleCard);

  /* -- machine state --------------------------------------------------- */
  const queueCard = card('Queue', { action: 'Open queue', onAction: () => goTo('queue') });
  queueCard.append(el('p', { className: 'ovloading', textContent: 'loading…' }));
  grid.append(queueCard);

  /* -- last output ----------------------------------------------------- */
  const runCard = card('Latest output', { action: 'Open result', onAction: () => goTo('result') });
  runCard.append(el('p', { className: 'ovloading', textContent: 'loading…' }));
  grid.append(runCard);

  // Adding an exemplar reloads this card, not the page.
  async function loadStyleCard() {
    const name = applied.at(-1);
    if (!name) {
      styleCard.replaceChildren(styleCard.firstChild,
        el('p', { className: 'empty', textContent:
          'No style applied. A style sheet is what keeps separate runs on-model.' }));
      return;
    }
    try {
      const detail = await api.styleDetail(name);
      styleCard.replaceChildren(styleCard.firstChild,
        el('h4', { textContent: 'Images' }),
        contextStrip(detail, loadStyleCard),
        el('h4', { textContent: 'Prompts' }),
        promptStrip(detail, loadStyleCard));
    } catch (e) {
      styleCard.replaceChildren(styleCard.firstChild,
        el('p', { className: 'warnline', textContent: e.message }));
    }
  }

  (async () => {
    await loadStyleCard();

    try {
      const q = await api.queue();
      const running = q.autopilot.running;
      queueCard.replaceChildren(queueCard.firstChild,
        el('div', { className: `pilotstate ${running ? 'on' : ''}` },
          el('span', { className: `dot ${running ? 'up' : ''}` }),
          el('b', { textContent: running ? 'Autopilot running' : 'Autopilot stopped' })),
        statLine([
          ['pending', q.counts.pending],
          ['running', q.counts.running, q.counts.running ? 'go' : ''],
          ['held', q.counts.held],
          ['failed', q.counts.failed, q.counts.failed ? 'bad' : ''],
        ]),
        q.services.ok ? null : el('p', { className: 'warnline',
          textContent: `⚠ ${q.services.why}` }));
    } catch (e) {
      queueCard.replaceChildren(queueCard.firstChild,
        el('p', { className: 'warnline', textContent: e.message }));
    }

    try {
      const { runs } = await api.runs();
      const latest = runs[0];
      if (!latest) {
        runCard.replaceChildren(runCard.firstChild,
          Empty('Nothing generated yet.'));
      } else {
        const shots = [];
        for (const stage of [...latest.stages].reverse()) {
          for (const image of stage.images.slice(0, 4)) {
            const base = state.system?.paths?.output_dir || 'out/runs';
            shots.push(`${base}/${latest.id}/${stage.dir}/${image}`);
          }
          if (shots.length >= 4) break;
        }
        const strip = el('div', { className: 'ovshots' },
          ...shots.slice(0, 4).map((p) =>
            el('img', { src: api.fileUrl(p), loading: 'lazy', className: 'pixel' })));
        runCard.replaceChildren(runCard.firstChild,
          el('div', { className: 'mini', textContent:
            `${latest.id}${latest.running ? ' · running' : ''}`
            + (latest.stopped_at ? ` · gated at ${latest.stopped_at}` : '') }),
          strip,
          (() => {
            const b = Button('Refine in editor', { variant: 'ghost' });
            b.onclick = () => goTo('editor');
            return b;
          })());
      }
    } catch (e) {
      runCard.replaceChildren(runCard.firstChild,
        el('p', { className: 'warnline', textContent: e.message }));
    }
  })();
}
