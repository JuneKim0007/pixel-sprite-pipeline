/* Overview: the handful of things actually done every session, on one screen.
 *
 * The tab list grew to eight, and eight tabs is a menu you read rather than a
 * tool you reach for. Most sessions are the same four moves — check what the
 * machine is doing, look at the last thing it made, adjust the look, start
 * another — and those were spread across four views with no landing surface
 * that showed the state of any of them.
 *
 * So this is a dashboard, not a fifth place to configure things. Every card
 * shows live state and hands off to the view that owns the detail; nothing is
 * edited here that cannot be edited better elsewhere. The one exception is the
 * style context strip, which is here because adding a reference image is the
 * single most frequent edit and burying it three clicks deep made it feel like
 * an administrative act rather than part of drawing.
 */

import { api } from './api.js';
import { el, state, toast } from './store.js';

function card(title, { action, onAction } = {}) {
  const head = el('div', { className: 'ovhead' }, el('h2', { textContent: title }));
  if (action) {
    const b = el('button', { className: 'btn ghost', textContent: action });
    b.onclick = onAction;
    head.append(b);
  }
  return el('section', { className: 'ovcard' }, head);
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
    return el('p', { className: 'empty', textContent: 'No style applied to this pipeline.' });
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
      } catch (e) { toast(e.message, 'error'); }
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
    } catch (e) { toast(e.message, 'error'); }
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
    } catch (e) { toast(e.message, 'error'); }
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
    box.append(el('p', { className: 'empty', textContent: 'No vocabulary groups.' }));
  }
  box.append(el('p', { className: 'help', textContent:
    'Enter adds a fragment, × removes one. These substitute into {placeholders} '
    + 'in the module templates; every edit lands in the style sheet and leaves '
    + 'a history entry.' }));
  return box;
}

/* ------------------------------------------------------------------ view */

export function renderOverview(host, { goTo }) {
  const refresh = () => renderOverview(host, { goTo });
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
        const b = el('button', { className: 'btn primary', textContent: 'Set up a run' });
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

  (async () => {
    // Style context, with its images and prompts editable in place.
    const name = applied.at(-1);
    if (!name) {
      styleCard.replaceChildren(styleCard.firstChild,
        el('p', { className: 'empty', textContent:
          'No style applied. A style sheet is what keeps separate runs on-model.' }));
    } else {
      try {
        const detail = await api.styleDetail(name);
        styleCard.replaceChildren(styleCard.firstChild,
          el('h4', { textContent: 'Images' }),
          contextStrip(detail, refresh),
          el('h4', { textContent: 'Prompts' }),
          promptStrip(detail, refresh));
      } catch (e) {
        styleCard.replaceChildren(styleCard.firstChild,
          el('p', { className: 'warnline', textContent: e.message }));
      }
    }

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
          el('p', { className: 'empty', textContent: 'Nothing generated yet.' }));
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
            const b = el('button', { className: 'btn ghost', textContent: 'Refine in editor' });
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
