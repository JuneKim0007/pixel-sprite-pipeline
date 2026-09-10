// Result tab — per-stage sections, each viewable as a grid, an animation, or a joined sheet.

import { api } from '../../api.js';
import { showError } from '../../core/errors.js';
import { el } from '../../core/dom.js';
import { state, toast } from '../../store.js';
import { confirmDialog, lightbox } from '../../ui/dialog.js';
import { Button, Disclosure, Empty, Meter, PanelHead } from '../../ui/index.js';
import { GpuProgress, RunProgress } from '../../features/progress.js';
import { browseDialog } from '../../ui/dialog.js';

const STAGE_LABEL = {
  pose: 'Pose guides', depth: 'Depth maps', canonical: 'Reference sprite',
  frames: 'Generated frames', softbody: 'Secondary motion',
  palette: 'Pixelized', export: 'Sprite sheet',
};

const STAGE_NOTE = {
  pose: 'Layout guides telling the model where the parts go. Authored, not estimated — estimators fail on sprites.',
  depth: 'Computed from the pose, no model involved.',
  canonical: 'The identity anchor every frame refers back to.',
  frames: 'One per pose guide; only the guide varied.',
  palette: 'One shared palette imposed on every frame.',
};

const viewModes = new Map();   // stage dir -> 'grid' | 'anim' | 'strip'
const openStages = new Map();  // stage dir -> whether its panel is open


const PROTOCOL = {
  character_sheet: { label: 'Sheet', icon: '▦' },
  animation: { label: 'Animation', icon: '▶' },
};

function runThumb(run) {
  for (const stage of [...run.stages].reverse()) {
    if (stage.images.length) {
      return `${state.system?.paths?.output_dir || 'out/runs'}/${run.id}/${stage.dir}/${stage.images[0]}`;
    }
  }
  return null;
}

function historyCard(run, { selected, onPick }) {
  const proto = PROTOCOL[run.audit?.protocol] || { label: run.audit?.protocol || '—', icon: '·' };
  const card = el('button', { className: `histcard ${selected ? 'on' : ''}` });
  card.onclick = () => onPick(run.id);

  const thumb = runThumb(run);
  const name = run.id.replace(/^\d{8}_\d{6}_/, '') || run.id;
  card.append(
    thumb ? el('img', { src: api.fileUrl(thumb), loading: 'lazy', className: 'pixel' })
          : el('div', { className: 'histblank', textContent: '·' }),
    el('div', { className: 'histmeta' },
      el('span', { className: 'histname', textContent: name, title: run.audit?.subject || '' }),
      el('span', { className: 'mini', textContent: run.modified.replace('T', ' ').slice(5, 16) })));
  card.title = `${name}${run.audit?.subject ? ` — ${run.audit.subject}` : ''}\n${proto.label}`;

  if (run.running) card.append(el('span', { className: 'histbadge run', textContent: '●' }));
  else if (run.stopped_at) card.append(el('span', { className: 'histbadge gate', textContent: '⏸' }));
  return card;
}

/* The only way to stop a run was the terminal; the button the API already had
 * was never called from anywhere. */
function abort(runId) {
  const button = Button('Stop run', { variant: 'ghost',
    title: 'Ask the run to stop after the task it is on' });
  button.onclick = async () => {
    button.disabled = true;
    try {
      await api.stop(runId);
      toast('Stopping — it finishes the task it is on first');
    } catch (e) {
      button.disabled = false;
      showError(e);
    }
  };
  return button;
}


function auditPanel(detail) {
  const a = detail.audit || {};
  if (!Object.keys(a).length) {
    return el('p', { className: 'mini', textContent: 'This run recorded no config.' });
  }
  const proto = PROTOCOL[a.protocol] || { label: a.protocol };
  const ctx = a.contexts || {};
  const roles = ['identity', 'style', 'pose', 'palette']
    .filter((r) => ctx[r]).map((r) => `${ctx[r]} ${r}`);
  if (ctx.style_exemplars) roles.push(`${ctx.style_exemplars} from sheets`);

  const line = (k, v, mono) => el('div', { className: 'auditrow' },
    el('span', { className: 'mini', textContent: k }),
    el('span', { className: mono ? 'mono' : '', textContent: v }));

  return el('div', { className: 'auditgrid' },
    line('protocol', proto.label),
    line('style sheets', (a.styles || []).join(' + ') || 'none'),
    line('contexts', roles.length ? `${a.context_total} · ${roles.join(', ')}` : 'none'),
    line('rig', a.rig || '—'),
    line('stages', (a.stages || []).join(' → ')),
    line('checkpoint', (a.models?.checkpoint || '').replace('.safetensors', ''), true),
    line('vae', (a.models?.vae || '').replace('.safetensors', ''), true),
    line('seed', a.seed ?? '—', true),
    line('palette', a.palette?.source
      ? `${a.palette.source} · ${a.palette.size ?? '?'} colours · ÷${a.palette.factor ?? '?'}`
        + (a.palette.match ? ` · ${a.palette.match}` : '')
      : '—'),
    a.subject ? el('p', { className: 'auditsubject', textContent: a.subject }) : null);
}

export function failureFrom(log) {
  if (!log || !/Traceback \(most recent call last\)/.test(log)) return null;

  const lines = log.trimEnd().split('\n');
  let where = '';
  for (const line of lines) {
    const at = /pipeline\/stages\/(\w+)\.py/.exec(line);
    if (at) where = at[1];
  }

  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;
    const named = /^(?:[\w.]+\.)?([A-Za-z_]\w*):\s*(.+)$/.exec(line);
    return named
      ? { kind: named[1], message: named[2], where }
      : { kind: 'Failed', message: line, where };
  }
  return null;
}

function failureBanner(failure, onShowLog, produced = false) {
  const where = failure.where ? ` in ${failure.where}` : '';
  const what = produced
    ? `This run stopped${where}. What is below came from the stages before it`
    : `This run failed${where}`;
  const box = el('div', { className: `banner ${produced ? 'warn' : 'err'} failed` },
    el('div', {},
      el('b', { textContent: `${what}: ${failure.kind}` }),
      el('p', { className: 'mini', textContent: failure.message })));
  const jump = Button('Log', { variant: 'ghost' });
  jump.onclick = onShowLog;
  box.append(jump);
  return box;
}

function grid(runId, stage) {
  const box = el('div', { className: 'thumbs' });
  for (const name of stage.images) {
    const path = `${state.runDir}/${stage.dir}/${name}`;
    const src = api.fileUrl(path);
    const card = el('div', { className: 'thumb' },
      el('img', { src, loading: 'lazy' }),
      el('div', { className: 'cap', textContent: name }));
    card.onclick = () => lightbox(src, `${stage.name} · ${name}`);
    box.append(card);
  }
  return box;
}

/** Frame player: the only way to judge whether an animation actually reads. */
function animation(runId, stage, stops) {
  const srcs = stage.images.map((n) => api.fileUrl(`${state.runDir}/${stage.dir}/${n}`));
  if (!srcs.length) return Empty('No frames.');

  const view = el('img', { className: 'animview', src: srcs[0] });
  const scrub = el('input', { type: 'range', min: 0, max: srcs.length - 1, step: 1, value: 0 });
  const fps = el('input', { type: 'range', min: 1, max: 24, step: 1, value: 12 });
  const fpsLabel = el('span', { className: 'mono', textContent: '12 fps' });
  const counter = el('span', { className: 'mono', textContent: `1/${srcs.length}` });
  const playBtn = Button('▶ Play');
  const loop = el('input', { type: 'checkbox', checked: true });

  let index = 0, timer = null;

  const show = (i) => {
    index = i;
    view.src = srcs[i];
    scrub.value = i;
    counter.textContent = `${i + 1}/${srcs.length}`;
  };

  const tick = () => {
    let next = index + 1;
    if (next >= srcs.length) {
      if (!loop.checked) return stop();
      next = 0;
    }
    show(next);
  };

  const start = () => {
    stop();
    timer = setInterval(tick, 1000 / Number(fps.value));
    playBtn.textContent = '❚❚ Pause';
  };
  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
    playBtn.textContent = '▶ Play';
  };

  playBtn.onclick = () => (timer ? stop() : start());
  scrub.oninput = () => { stop(); show(Number(scrub.value)); };
  fps.oninput = () => {
    fpsLabel.textContent = `${fps.value} fps`;
    if (timer) start();
  };

  const wrap = el('div', { className: 'player' },
    view,
    el('div', { className: 'playerbar' },
      playBtn, counter,
      el('span', { className: 'mini', textContent: 'frame' }), scrub,
      el('span', { className: 'mini', textContent: 'speed' }), fps, fpsLabel,
      el('label', { className: 'chk' }, loop, ' loop')));
  // The view hands this to the lifecycle.
  stops.push(stop);
  return wrap;
}

/** Joined sheet, drawn client-side so any stage can be viewed this way. */
function consumedStrip(paths) {
  const box = el('div', { className: 'consumed' });
  for (const path of paths.slice(0, 12)) {
    box.append(el('img', {
      src: api.fileUrl(path), loading: 'lazy', title: path.split('/').pop(),
    }));
  }
  return el('div', {},
    el('p', { className: 'mini', textContent: `Consumed ${paths.length} file(s) from an earlier stage` }),
    box);
}

function strip(runId, stage) {
  const canvas = el('canvas', { className: 'sheetcanvas' });
  const srcs = stage.images.map((n) => api.fileUrl(`${state.runDir}/${stage.dir}/${n}`));
  if (!srcs.length) return Empty('Nothing to join.');

  Promise.all(srcs.map((src) => new Promise((res) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = () => res(null);
    img.src = src;
  }))).then((images) => {
    const ok = images.filter(Boolean);
    if (!ok.length) return;
    const w = Math.max(...ok.map((i) => i.naturalWidth));
    const h = Math.max(...ok.map((i) => i.naturalHeight));
    canvas.width = w * ok.length;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    ok.forEach((img, i) => ctx.drawImage(img, i * w, 0));
  });

  return canvas;
}

async function downloadStage(runId, stageName) {
  const target = await browseDialog(state.system?.paths?.download_dir || '', false);
  if (!target) return;

  const plan = await api.downloadPlan({ run_id: runId, stage: stageName, target });
  let overwrite = false;

  if (plan.conflicts.length) {
    const names = plan.conflicts.slice(0, 6).map((c) => c.name).join(', ');
    const { ok } = await confirmDialog({
      title: 'Files already exist',
      body: `<p><b>${plan.conflicts.length}</b> of ${plan.total} file(s) already exist in`
          + ` <code>${plan.target}</code>:</p><p class="mono small">${names}`
          + `${plan.conflicts.length > 6 ? ' …' : ''}</p>`
          + `<p>Overwrite them, or keep both by adding a numbered suffix?</p>`,
      confirmLabel: 'Overwrite',
    });
    overwrite = ok;
  }

  const res = await api.download({ run_id: runId, stage: stageName, target, overwrite });
  toast(`Copied ${res.written.length} file(s)`
    + (res.renamed.length ? `, ${res.renamed.length} renamed to avoid clobbering` : ''));
}

const EDITABLE = {
  pose: { label: 'Edit pose guides', tab: 'run', step: 1 },
};

// The gate banner, with the actions inline.
function gateBanner(runId, detail) {
  const stage = detail.stopped_at;
  const editable = EDITABLE[stage];
  const remaining = (detail.stages || []).length;

  const resume = Button('Run the rest', { variant: 'primary' });
  resume.onclick = async () => {
    resume.disabled = true;
    try {
      await api.start({ resume: runId });
      toast(`Resumed ${runId}`);
      window.dispatchEvent(new CustomEvent('pipeline:resumed', { detail: { runId } }));
    } catch (e) {
      showError(e);
      resume.disabled = false;
    }
  };

  const actions = [resume];
  if (editable) {
    const edit = Button(editable.label);
    edit.onclick = () => {
      window.dispatchEvent(new CustomEvent('pipeline:edit', {
        detail: { runId, tab: editable.tab, step: editable.step },
      }));
    };
    actions.unshift(edit);
  }

  return el('div', { className: 'banner warn gatebanner' },
    el('div', {},
      el('b', {}, `Paused after "${stage}".`),
      ' ',
      editable
        ? `Adjust it below, or run the remaining ${remaining ? '' : ''}stages.`
        : 'Nothing here needs editing — continue when ready.'),
    el('div', { className: 'banner-actions' }, ...actions));
}

export function renderResult(host, { runId, detail, onPick }) {
  host.replaceChildren();
  const stops = [];

  const history = el('div', { className: 'histstrip' });
  const historyBox = el('section', { className: 'histbox' },
    PanelHead('History'),
    history);
  host.append(historyBox);

  (async () => {
    try {
      const { runs } = await api.runs();
      if (!runs.length) {
        history.append(Empty('Nothing generated yet.'));
        return;
      }
      for (const run of runs.slice(0, 40)) {
        history.append(historyCard(run, {
          selected: run.id === runId,
          onPick: (id) => onPick?.(id),
        }));
      }
    } catch (e) {
      history.append(el('p', { className: 'warnline', textContent: e.message }));
    }
  })();

  if (!detail || !detail.stages?.length) {
    host.append(Empty('No output yet. Start a run.'));
    return;
  }

  // Two questions with two lifetimes: what the GPU is doing now, and how far the run has come.
  const gpuBox = el('div', {});
  const runBox = el('div', {});
  const feeds = [
    new GpuProgress(api, (s) => gpuBox.replaceChildren(Meter(s))),
    new RunProgress(api, (s) => runBox.replaceChildren(Meter(s))),
  ];
  for (const feed of feeds) feed.start();
  stops.push(() => { for (const feed of feeds) feed.end(); });

  host.append(el('section', { className: 'group' },
    PanelHead('Progress'),
    el('div', { className: 'fields progressbox' }, gpuBox, runBox)));

  host.append(el('section', { className: 'auditbox' },
    PanelHead(runId, { note: detail.dir, action: detail.running ? abort(runId) : null }),
    auditPanel(detail)));

  state.runDir = detail.dir;

  const logPanel = el('pre', { className: 'log', textContent: detail.log || '(no log)' });
  const failure = failureFrom(detail.log);
  const produced = (detail.stages || []).some((s) => s.images?.length);

  const toLog = () => {
    logPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    logPanel.scrollTop = logPanel.scrollHeight;
  };

  if (detail.running) {
    host.append(el('div', { className: 'banner' },
      'Running — output appears as each stage finishes.'));
  } else if (failure) {
    // Shown whether or not earlier stages produced anything.
    host.append(failureBanner(failure, toLog, produced));
    if (detail.stopped_at) host.append(gateBanner(runId, detail));
  } else if (detail.stopped_at) {
    host.append(gateBanner(runId, detail));
  }

  for (const stage of detail.stages) {
    const mode = viewModes.get(stage.dir) || 'grid';
    const body = el('div', { className: 'stagebody' });

    const buttons = ['grid', 'anim', 'strip'].map((m) => {
      const btn = el('button', {
        className: `segbtn ${mode === m ? 'on' : ''}`,
        textContent: { grid: 'Grid', anim: 'Animation', strip: 'Strip' }[m],
      });
      btn.onclick = () => {
        viewModes.set(stage.dir, m);
        renderResult(host, { runId, detail });
      };
      return btn;
    });

    const dl = Button('Download', { variant: 'ghost' });
    dl.onclick = () => downloadStage(runId, stage.name).catch((e) => showError(e));

    body.append(
      mode === 'grid' ? grid(runId, stage)
      : mode === 'anim' ? animation(runId, stage, stops)
      : strip(runId, stage));

    const fed = (detail.consumed || {})[stage.name] || [];
    host.append(Disclosure(STAGE_LABEL[stage.name] || stage.name, {
      open: openStages.get(stage.dir) ?? true,
      note: `${stage.dir} · ${stage.images.length} file(s)`,
      actions: [el('span', { className: 'seg' }, ...buttons), dl],
      onToggle: (v) => openStages.set(stage.dir, v),
    },
      STAGE_NOTE[stage.name]
        ? el('p', { className: 'help stagehelp', textContent: STAGE_NOTE[stage.name] })
        : null,
      fed.length ? consumedStrip(fed) : null,
      body));
  }

  host.append(el('section', { className: 'group' },
    el('h2', { textContent: 'Log' }),
    el('div', { className: 'fields' }, logPanel)));
  logPanel.scrollTop = logPanel.scrollHeight;

  return () => { for (const stop of stops) stop(); };
}
