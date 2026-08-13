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

import { api } from '../../api.js';
import { el } from '../../core/dom.js';
import { state, toast } from '../../store.js';

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
    detail.foldered ? null : el('p', { className: 'warnline', textContent:
      `⚠ Single YAML file — no exemplar folder. Move it to `
      + `styles/${detail.name}/style.yaml.` }));

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
      el('span', { className: 'count', textContent: String(groups.length) })));

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
  const chips = el('div', { className: 'histfilter' });
  const body = el('div', {});

  const counts = { all: events.length };
  for (const k of Object.keys(KINDS)) {
    counts[k] = events.filter((e) => e.kind === k).length;
  }

  // Filtering swaps the chips and the timeline. It used to redraw the whole
  // screen, which reloaded the sheet list and the detail panel for a change
  // that touches neither.
  function draw() {
    chips.replaceChildren(...['all', ...Object.keys(KINDS)].map((key) => {
      const chip = el('button', {
        className: `chip ${filter === key ? 'on' : ''}`,
        textContent: `${key === 'all' ? 'All' : KINDS[key].label} ${counts[key]}`,
        disabled: counts[key] === 0 && key !== 'all',
      });
      chip.onclick = () => { filter = key; draw(); };
      return chip;
    }));

    const shown = filter === 'all' ? events : events.filter((e) => e.kind === filter);
    if (!shown.length) {
      body.replaceChildren(el('p', { className: 'empty', textContent:
        events.length ? 'Nothing of that kind yet.'
          : 'Nothing recorded yet. Applying, tuning and training this look will '
            + 'each leave an entry.' }));
      return;
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
    body.replaceChildren(timeline);
  }

  panel.append(chips);

  if (!detail.foldered) {
    panel.append(el('p', { className: 'empty', textContent:
      `A single-file sheet has nowhere to keep a history. Move it to `
      + `styles/${detail.name}/style.yaml and it starts recording.` }));
    draw();
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
  panel.append(el('div', { className: 'histadd' }, input, add), body);

  draw();
  return panel;
}

/* -------------------------------------------------------------- training
 *
 * Guidance and a reading of what is actually staged, in one panel. Guidance
 * alone is a document nobody opens; a verdict alone does not say what to do
 * about it. The pairing is the point: the rule and the image that breaks it
 * sit on the same screen.
 */

const BAND = {
  sprite: { label: 'sprite', tone: 'ok' },
  clean:  { label: 'clean', tone: 'ok' },
  soft:   { label: 'soft', tone: 'warn' },
  render: { label: 'render', tone: 'bad' },
};

function targetCard(t) {
  const card = el('div', { className: `traincard ${t.shared_with ? 'shared' : ''}` },
    el('div', { className: 'trainhead' },
      el('b', { textContent: t.label }),
      t.shared_with
        ? el('span', { className: 'tagpill', textContent: `same set as ${t.shared_with}` })
        : el('span', { className: 'mini', textContent: t.count })),
    el('p', { className: 'help', textContent: t.teaches }));

  const section = (title, items, kind) => {
    if (!items?.length) return null;
    return el('div', { className: `trainlist ${kind}` },
      el('h5', { textContent: title }),
      el('ul', {}, ...items.map((s) => el('li', { textContent: s }))));
  };

  card.append(
    section('Vary', t.vary, 'vary'),
    section('Hold constant', t.hold, 'hold'),
    section('Never include', t.reject, 'reject'));
  if (t.caption) {
    card.append(el('p', { className: 'captionrule' },
      el('b', { textContent: 'Captions: ' }),
      el('span', { textContent: t.caption })));
  }
  return card;
}

function stagedRow(image) {
  const band = BAND[image.band] || BAND.render;
  const row = el('div', { className: `stagedrow ${image.warnings.length ? 'flagged' : ''}` },
    el('img', { src: api.fileUrl(image.path), loading: 'lazy', alt: image.name }),
    el('div', { className: 'stagedmain' },
      el('div', { className: 'stagedname' },
        el('span', { textContent: image.name }),
        el('span', { className: `bandpill ${band.tone}`, textContent: band.label })),
      el('div', { className: 'mini', textContent:
        `${image.width}×${image.height} · figure ${image.figure_height}px · `
        + `${image.colours?.toLocaleString()} colours` }),
      ...image.warnings.map((w) => el('p', { className: 'warnline', textContent: `⚠ ${w}` })),
      // Notes are things that look alarming and are not. Saying so beats
      // leaving them out, because the next person measures the same number.
      ...(image.notes || []).map((n) => el('p', { className: 'mini soft', textContent: `· ${n}` }))));
  return row;
}

async function trainingPanel(name) {
  const panel = el('div', { className: 'trainpanel' });
  let data;
  try {
    data = await api.styleTraining(name);
  } catch (e) {
    panel.append(el('p', { className: 'empty', textContent: e.message }));
    return panel;
  }

  const v = data.verdict;
  panel.append(el('div', { className: `verdict ${v.ready ? 'ok' : 'notready'}` },
    el('div', { className: 'verdicthead' },
      el('b', { textContent: v.ready ? 'Ready to train' : 'Not ready to train' }),
      el('span', { className: 'mini', textContent: `${v.count} staged` })),
    ...v.problems.map((p) => el('p', { className: 'warnline', textContent: `✗ ${p}` })),
    ...v.notes.map((n) => el('p', { className: 'mini', textContent: `· ${n}` }))));

  // The plan is the actionable half of the verdict: not "these disagree" but
  // "reduce this one by 2". Feature scale is measured, so the factor is known
  // rather than guessed.
  const plan = data.plan || { steps: [] };
  if (plan.steps.length || plan.clean) {
    const box = el('div', { className: 'planbox' },
      el('div', { className: 'planhead' },
        el('b', { textContent: 'Normalisation plan' }),
        el('span', { className: 'mini', textContent:
          `${plan.clean} conform · target 1px blocks, figure ${plan.target_height}px` })),
      );
    for (const step of plan.steps) {
      box.append(el('div', { className: 'planstep' },
        el('code', { textContent: step.name }),
        el('div', {}, ...step.actions.map((a) => el('div', { className: 'planaction' },
          el('span', { className: `actionpill ${a.kind}`,
                       textContent: `${a.kind} ×${a.factor}` }),
          el('span', { className: 'mini', textContent: a.why }))))));
    }
    if (!plan.steps.length) {
      box.append(el('p', { className: 'ok', textContent:
        '✓ Every staged image is already at one logical pixel per image pixel.' }));
    }
    panel.append(box);
  }

  panel.append(el('div', { className: 'traincards' },
    ...data.targets.map(targetCard)));

  panel.append(el('h3', { className: 'stagedhead' },
    el('span', { textContent: 'Staged images' }),
    el('span', { className: 'count', textContent: String(data.staged.length) })));
  panel.append(el('p', { className: 'help', textContent:
    `Drop files into ${data.dir}. Nothing here is read at generation time — `
    + 'staging accumulates until there is enough to train on.' }));

  if (!data.staged.length) {
    panel.append(el('p', { className: 'empty', textContent: 'Nothing staged yet.' }));
  } else {
    panel.append(el('div', { className: 'stagedlist' }, ...data.staged.map(stagedRow)));
  }
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
    for (const [key, label] of [['context', 'Context'], ['training', 'Training'],
                                ['history', 'History'], ['resolved', 'Resolved']]) {
      const b = el('button', {
        className: `seg ${tab === key ? 'on' : ''}`,
        textContent: key === 'history' && detail.history.length
          ? `${label} ${detail.history.length}`
          : key === 'training' && detail.training.pending
            ? `${label} ${detail.training.pending}` : label,
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
    else if (tab === 'training') panelHost.append(await trainingPanel(detail.name));
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
