/* Style Manager: named looks, what applying one does, and what has been done to it.
 *
 * A style sheet is not only prompts — it can pin a palette, a LoRA strength, a
 * sampler, and carry exemplar images, because all of those carry a look. That
 * breadth is the point, and it is also the risk: applying one can quietly
 * change a setting you had chosen deliberately.
 *
 * The tab is master-detail: the sheets on the left, one sheet's detail on the
 * right, and the detail splits three ways because a look raises three separate
 * questions.
 *
 *   Context    what is true now, and editable. Split again into images and
 *              prompts, because they are edited differently — one is a folder
 *              you drop files into, the other is text in a document.
 *   History    what happened, and is not editable. An append-only audit trail.
 *   Resolved   what the pipeline would actually send, and what that overrides.
 *
 * Keeping Context and History apart is the whole design. Mixed together, the
 * current exemplars and the record of exemplars-since-removed read as one
 * ambiguous pile, and the question the history exists to answer — "what
 * changed, and when did this look get worse?" — becomes unanswerable.
 *
 * The history deliberately offers no restore. A `train` entry keeps the
 * dataset's manifest, not the dataset: names, sizes, hashes. That is evidence
 * that the training happened on those files, not a button that would re-run
 * it, and it is presented as evidence — archived, dimmed, inert — so nobody
 * mistakes the difference.
 */

import { api } from './api.js';
import { el, state, toast } from './store.js';

const KINDS = {
  context: { icon: '◧', label: 'Context', hint: 'an exemplar or note changed' },
  tune:    { icon: '⇄', label: 'Tune',    hint: 'a setting moved, with evidence' },
  train:   { icon: '◆', label: 'Train',   hint: 'a LoRA was produced' },
  note:    { icon: '✎', label: 'Note',    hint: 'written down by hand' },
};

let selected = null;      // which sheet the detail pane is showing
let tab = 'context';      // which sub-view of that sheet
let filter = 'all';       // history filter

const bytes = (n) => (n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB`
  : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);

function when(iso) {
  if (!iso) return 'unknown time';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/* ------------------------------------------------------------ the selector */

function sheetRow(sheet, { applied, active, onPick, onToggle }) {
  const row = el('div', { className: `sheetrow ${active ? 'active' : ''}` });
  row.onclick = () => onPick(sheet.name);

  const toggle = el('button', {
    className: `pill ${applied ? 'on' : ''}`,
    textContent: applied ? 'applied' : 'apply',
    title: applied ? 'Remove from this pipeline' : 'Apply to this pipeline',
  });
  toggle.onclick = (e) => { e.stopPropagation(); onToggle(sheet.name, !applied); };

  const marks = [];
  if (sheet.foldered) marks.push('◧');
  if (sheet.lora?.name) marks.push('◆');
  if (sheet.training_images) marks.push(`${sheet.training_images}↑`);

  row.append(
    el('div', { className: 'sheetmain' },
      el('b', { textContent: sheet.label }),
      el('div', { className: 'path', textContent: sheet.name })),
    el('div', { className: 'sheetmarks', textContent: marks.join(' ') }),
    toggle);
  return row;
}

/* --------------------------------------------------------------- context */

function imagesPanel(detail) {
  const { images } = detail.context;
  const box = el('div', { className: 'ctxcol' },
    el('h3', {},
      el('span', { textContent: 'Images' }),
      el('span', { className: 'count', textContent: String(images.length) })),
    el('p', { className: 'help', textContent:
      detail.foldered
        ? `Drop files into ${detail.home}/context/exemplars/ — they are picked up `
          + 'automatically, with no edit to the YAML. These drive IP-Adapter at a '
          + 'deliberately weak weight: they say how the art should look, not who '
          + 'the character is.'
        : 'This sheet is a single YAML file, so it has no exemplar folder. '
          + `Move it to styles/${detail.name}/style.yaml to give it one.` }));

  if (!images.length) {
    box.append(el('p', { className: 'empty', textContent: 'No exemplars yet.' }));
    return box;
  }

  const grid = el('div', { className: 'ctxgrid' });
  for (const image of images) {
    const cell = el('figure', { className: `ctxcell ${image.missing ? 'gone' : ''}` });
    cell.append(image.missing
      ? el('div', { className: 'ctxmissing', textContent: '⚠' })
      : el('img', { src: api.fileUrl(image.path), loading: 'lazy', alt: image.name }));
    cell.append(el('figcaption', {},
      el('span', { className: 'name', textContent: image.name, title: image.path }),
      el('span', { className: 'mini', textContent:
        image.missing ? 'file is gone' : bytes(image.bytes) })));
    grid.append(cell);
  }
  box.append(grid);
  return box;
}

function promptsPanel(detail) {
  const { vocabulary, notes, token } = detail.context.prompts;
  const groups = Object.entries(vocabulary || {});

  const box = el('div', { className: 'ctxcol' },
    el('h3', {},
      el('span', { textContent: 'Prompts' }),
      el('span', { className: 'count', textContent: String(groups.length) })),
    el('p', { className: 'help', textContent:
      'Vocabulary groups are substituted into {placeholders} in the module '
      + 'templates. Inherited groups are merged, so a sheet only states what '
      + 'it changes.' }));

  if (token) {
    box.append(el('p', { className: 'mini', textContent: `trained token: ${token}` }));
  }

  if (!groups.length) {
    box.append(el('p', { className: 'empty', textContent: 'No vocabulary.' }));
  } else {
    const list = el('div', { className: 'vocab' });
    for (const [group, fragments] of groups) {
      list.append(el('div', { className: 'vocabrow' },
        el('span', { className: 'mini', textContent: group }),
        el('span', {}, ...fragments.map((f) =>
          el('span', { className: 'frag', textContent: f })))));
    }
    box.append(list);
  }

  box.append(el('h4', { textContent: 'Notes' }));
  box.append(notes
    ? el('pre', { className: 'notes', textContent: notes })
    : el('p', { className: 'empty', textContent:
        detail.foldered
          ? `No notes. Write ${detail.home}/context/notes.md — the orchestrator reads it.`
          : 'No notes.' }));
  return box;
}

function contextPanel(detail) {
  const panel = el('div', { className: 'ctxsplit' },
    imagesPanel(detail), promptsPanel(detail));

  const t = detail.training;
  if (t.pending || t.archives.length || t.lora?.name) {
    const facts = [];
    if (t.pending) facts.push(`${t.pending} image(s) staged for training`);
    if (t.lora?.name) facts.push(`LoRA ${t.lora.name}`);
    if (t.archives.length) {
      facts.push(`${t.archives.length} archived set(s)`);
    }
    panel.append(el('div', { className: 'ctxfoot' },
      el('span', { className: 'mini', textContent: facts.join(' · ') })));
  }
  return panel;
}

/* --------------------------------------------------------------- history */

function manifestTable(dataset) {
  const table = el('table', { className: 'manifest' });
  table.append(el('thead', {}, el('tr', {},
    el('th', { textContent: 'file' }),
    el('th', { textContent: 'size' }),
    el('th', { textContent: 'sha256' }))));
  const body = el('tbody');
  for (const f of dataset.files || []) {
    body.append(el('tr', {},
      el('td', { textContent: f.name }),
      el('td', { className: 'num', textContent: bytes(f.bytes) }),
      el('td', { className: 'hash', textContent: (f.sha256 || '').slice(0, 12) })));
  }
  table.append(body);
  return table;
}

function eventDetail(event) {
  const box = el('div', { className: 'evdetail' });

  if (event.kind === 'train') {
    const d = event.dataset || {};
    box.append(el('p', { className: 'evidence' },
      el('span', { className: 'lock', textContent: '⛒' }),
      el('span', { textContent:
        'Evidence, not a restore point. The dataset was archived after training; '
        + 'this entry records what it was. Re-running from here would not '
        + 'reproduce the weights — the seed, the optimiser state and the library '
        + 'versions are not recoverable.' })));

    if (event.lora) {
      box.append(el('p', { className: 'mini', textContent:
        `${event.lora.name} · ${bytes(event.lora.bytes)} · ${(event.lora.sha256 || '').slice(0, 12)}` }));
    }
    box.append(el('p', { className: 'mini', textContent:
      `${d.count || 0} images · ${bytes(d.bytes || 0)} · set ${(d.sha256 || '').slice(0, 12)}`
      + (event.archived_to ? ` · archived to ${event.archived_to}` : '') }));
    if ((d.files || []).length) box.append(manifestTable(d));
    if (Object.keys(event.settings || {}).length) {
      box.append(el('pre', { className: 'notes',
        textContent: JSON.stringify(event.settings, null, 1) }));
    }
    return box;
  }

  if (event.kind === 'tune') {
    box.append(el('div', { className: 'delta' },
      el('code', { textContent: event.axis }),
      el('span', { className: 'from', textContent: String(event.before) }),
      el('span', { className: 'arrow', textContent: '→' }),
      el('span', { className: 'to', textContent: String(event.after) })));

    const seeds = event.seeds || [];
    const subjects = event.subjects || [];
    box.append(el('p', { className: 'mini', textContent:
      `${event.trials || 0} trial(s)`
      + (subjects.length ? ` across ${subjects.join(', ')}` : '')
      + (seeds.length ? ` · seeds ${seeds.join(', ')}` : '') }));

    // A comparison whose variants had different seeds measured seed luck, not
    // the setting. Saying so here is cheaper than discovering it later.
    if (!seeds.length) {
      box.append(el('p', { className: 'warnline', textContent:
        '⚠ No seeds recorded, so this change is not evidence — variants that '
        + 'differ in seed as well as setting cannot tell you which one won.' }));
    } else if (new Set(seeds).size !== 1 && seeds.length !== subjects.length) {
      box.append(el('p', { className: 'warnline', textContent:
        '⚠ Seeds vary within the comparison; the effect may be seed luck.' }));
    }
    return box;
  }

  if (event.kind === 'context') {
    for (const [label, list] of [['added', event.added], ['removed', event.removed]]) {
      if ((list || []).length) {
        box.append(el('p', { className: 'mini', textContent: `${label}: ${list.join(', ')}` }));
      }
    }
    if ((event.files || []).length) box.append(manifestTable({ files: event.files }));
    return box;
  }

  return null;
}

function eventRow(event) {
  const kind = KINDS[event.kind] || KINDS.note;
  const row = el('div', { className: `event ${event.kind} ${event.corrupt ? 'corrupt' : ''}` });

  const detail = eventDetail(event);
  const head = el('div', { className: 'evhead' },
    el('span', { className: 'evicon', textContent: kind.icon, title: kind.hint }),
    el('div', { className: 'evmain' },
      el('div', { className: 'evsummary', textContent: event.summary || kind.label }),
      el('div', { className: 'evwhen', textContent: when(event.at) })),
    detail ? el('span', { className: 'evchevron', textContent: '▸' }) : null);

  row.append(head);
  if (detail) {
    detail.hidden = true;
    row.append(detail);
    head.style.cursor = 'pointer';
    head.onclick = () => {
      detail.hidden = !detail.hidden;
      head.querySelector('.evchevron').textContent = detail.hidden ? '▸' : '▾';
    };
  }
  return row;
}

function historyPanel(detail, rerender) {
  const events = detail.history || [];
  const panel = el('div', { className: 'histpanel' });

  const counts = { all: events.length };
  for (const k of Object.keys(KINDS)) {
    counts[k] = events.filter((e) => e.kind === k).length;
  }

  const chips = el('div', { className: 'histfilter' });
  for (const key of ['all', ...Object.keys(KINDS)]) {
    const chip = el('button', {
      className: `chip ${filter === key ? 'on' : ''}`,
      textContent: `${key === 'all' ? 'All' : KINDS[key].label} ${counts[key]}`,
      disabled: counts[key] === 0 && key !== 'all',
    });
    chip.onclick = () => { filter = key; rerender(); };
    chips.append(chip);
  }
  panel.append(chips);

  if (!detail.foldered) {
    panel.append(el('p', { className: 'empty', textContent:
      `A single-file sheet has nowhere to keep a history. Move it to `
      + `styles/${detail.name}/style.yaml and it starts recording.` }));
    return panel;
  }

  // Writing a note is the one thing you may add to an append-only log by hand.
  const input = el('input', { type: 'text', placeholder:
    'Note what you changed and why — "dropped to 24 colours, armour was reading flat"' });
  const add = el('button', { className: 'btn', textContent: 'Add note' });
  const submit = async () => {
    if (!input.value.trim()) return;
    try {
      await api.styleNote(detail.name, input.value);
      input.value = '';
      rerender(true);
    } catch (e) { toast(e.message, 'error'); }
  };
  add.onclick = submit;
  input.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
  panel.append(el('div', { className: 'histadd' }, input, add));

  const shown = filter === 'all' ? events : events.filter((e) => e.kind === filter);
  if (!shown.length) {
    panel.append(el('p', { className: 'empty', textContent:
      events.length ? 'Nothing of that kind yet.'
        : 'Nothing recorded yet. Applying, tuning and training this look will '
          + 'each leave an entry.' }));
    return panel;
  }

  const timeline = el('div', { className: 'timeline' });
  let day = '';
  for (const event of shown) {
    const stamp = (event.at || '').slice(0, 10);
    if (stamp !== day) {
      day = stamp;
      timeline.append(el('div', { className: 'histday', textContent: day || 'undated' }));
    }
    timeline.append(eventRow(event));
  }
  panel.append(timeline);
  return panel;
}

/* -------------------------------------------------------------- resolved */

async function resolvedPanel() {
  const panel = el('div', { className: 'group' },
    el('h2', { textContent: 'What will be sent' }));
  try {
    const preview = await api.stylePreview(state.current);
    panel.append(el('div', { className: 'fields' },
      el('p', { className: 'resolved mono', textContent:
        preview.resolved_prompt || '(nothing resolved yet)' }),
      preview.palette
        ? el('p', { className: 'mini', textContent: `palette: ${preview.palette}` })
        : null,
      ...(preview.conflicts || []).map((c) =>
        el('p', { className: 'warnline', textContent: `⚠ ${c}` })),
      (preview.conflicts || []).length === 0
        ? el('p', { className: 'ok', textContent: '✓ No settings conflict with this pipeline.' })
        : null));
  } catch (e) {
    panel.append(el('p', { className: 'empty', textContent: e.message }));
  }
  return panel;
}

/* ------------------------------------------------------------------ view */

export function renderStyles(host, { onChanged }) {
  host.replaceChildren();
  const applied = state.effective?.styles || [];

  const list = el('div', { className: 'sheetlist' });
  const detailHost = el('div', { className: 'styledetail' });

  host.append(
    el('header', { className: 'head' },
      el('div', {},
        el('h1', { textContent: 'Styles' }),
        el('p', { className: 'sub', textContent:
          `Applied to ${state.current}: ${applied.join(' + ') || 'none'}` }))),
    el('div', { className: 'stylesbody' }, list, detailHost));

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

  const showDetail = async (refetch = false) => {
    if (!selected) return;
    if (refetch || !showDetail.cache || showDetail.cache.name !== selected) {
      showDetail.cache = await api.styleDetail(selected);
    }
    const detail = showDetail.cache;

    const bar = el('div', { className: 'segmented' });
    for (const [key, label] of [['context', 'Context'], ['history', 'History'],
                                ['resolved', 'Resolved']]) {
      const b = el('button', {
        className: `seg ${tab === key ? 'on' : ''}`,
        textContent: key === 'history' && detail.history.length
          ? `${label} ${detail.history.length}` : label,
      });
      b.onclick = () => { tab = key; showDetail(); };
      bar.append(b);
    }

    const panelHost = el('div', { className: 'segpanel' });
    detailHost.replaceChildren(
      el('div', { className: 'detailhead' },
        el('div', {},
          el('h2', { textContent: detail.label }),
          el('div', { className: 'path', textContent:
            detail.foldered ? detail.home : `${detail.home}/${detail.name}.yaml` })),
        el('span', { className: `tagpill ${detail.foldered ? '' : 'flat'}`,
                     textContent: detail.foldered ? 'folder' : 'single file' })),
      bar, panelHost);

    if (tab === 'context') panelHost.append(contextPanel(detail));
    else if (tab === 'history') panelHost.append(historyPanel(detail, showDetail));
    else panelHost.append(await resolvedPanel());
  };

  (async () => {
    try {
      const { styles: sheets } = await api.styles();
      if (!sheets.length) {
        list.append(el('p', { className: 'empty', textContent:
          'No style sheets yet. Add styles/<name>/style.yaml.' }));
        return;
      }
      if (!selected || !sheets.some((s) => s.name === selected)) {
        selected = applied.at(-1) || sheets[0].name;
      }
      for (const sheet of sheets) {
        list.append(sheetRow(sheet, {
          applied: applied.includes(sheet.name),
          active: sheet.name === selected,
          onPick: (name) => {
            selected = name;
            for (const row of list.querySelectorAll('.sheetrow')) row.classList.remove('active');
            list.children[sheets.findIndex((s) => s.name === name)]?.classList.add('active');
            showDetail(true);
          },
          onToggle: toggle,
        }));
      }
      await showDetail(true);
    } catch (e) {
      list.append(el('p', { className: 'empty', textContent: e.message }));
    }
  })();
}
