/* Result tab — per-stage sections, each viewable as a grid, an animation, or
 * a joined sheet.
 *
 * Sections rather than filter chips: the stages are a sequence, and seeing
 * skeleton → depth → frames → pixelized stacked in order is how you find where
 * something went wrong. A chip filter hides exactly the comparison you want.
 */

import { api } from './api.js';
import { confirmDialog, el, lightbox, state, toast } from './store.js';
import { browseDialog } from './input.js';

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

const viewModes = new Map();   // stage dir -> 'grid' | 'anim' | 'sheet'

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
function animation(runId, stage) {
  const srcs = stage.images.map((n) => api.fileUrl(`${state.runDir}/${stage.dir}/${n}`));
  if (!srcs.length) return el('p', { className: 'empty', textContent: 'No frames.' });

  const view = el('img', { className: 'animview', src: srcs[0] });
  const scrub = el('input', { type: 'range', min: 0, max: srcs.length - 1, step: 1, value: 0 });
  const fps = el('input', { type: 'range', min: 1, max: 24, step: 1, value: 12 });
  const fpsLabel = el('span', { className: 'mono', textContent: '12 fps' });
  const counter = el('span', { className: 'mono', textContent: `1/${srcs.length}` });
  const playBtn = el('button', { className: 'btn', textContent: '▶ Play' });
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

  // Stop the timer when this section is torn down, or it keeps firing.
  const wrap = el('div', { className: 'player' },
    view,
    el('div', { className: 'playerbar' },
      playBtn, counter,
      el('span', { className: 'mini', textContent: 'frame' }), scrub,
      el('span', { className: 'mini', textContent: 'speed' }), fps, fpsLabel,
      el('label', { className: 'chk' }, loop, ' loop')));
  wrap.addEventListener('DOMNodeRemovedFromDocument', stop);
  new MutationObserver(() => { if (!wrap.isConnected) stop(); })
    .observe(document.body, { childList: true, subtree: true });
  return wrap;
}

/** Joined sheet, drawn client-side so any stage can be viewed this way. */
function sheet(runId, stage) {
  const canvas = el('canvas', { className: 'sheetcanvas' });
  const srcs = stage.images.map((n) => api.fileUrl(`${state.runDir}/${stage.dir}/${n}`));
  if (!srcs.length) return el('p', { className: 'empty', textContent: 'Nothing to join.' });

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

/* Which stages produce something a person can meaningfully edit before the
 * pipeline consumes it. A gate on a stage with no editable output only needs a
 * Continue button. */
const EDITABLE = {
  pose: { label: 'Edit pose guides', tab: 'run', step: 1 },
};

/** The gate banner, with the actions inline.
 *
 * Telling someone to "continue from the Run tab" makes them navigate, find the
 * right step, and remember why they went — when the two things they might want
 * are known here and are one click each. */
function gateBanner(runId, detail) {
  const stage = detail.stopped_at;
  const editable = EDITABLE[stage];
  const remaining = (detail.stages || []).length;

  const resume = el('button', { className: 'btn primary', textContent: 'Run the rest' });
  resume.onclick = async () => {
    resume.disabled = true;
    try {
      await api.start({ resume: runId });
      toast(`Resumed ${runId}`);
      window.dispatchEvent(new CustomEvent('pipeline:resumed', { detail: { runId } }));
    } catch (e) {
      toast(e.message, 'error');
      resume.disabled = false;
    }
  };

  const actions = [resume];
  if (editable) {
    const edit = el('button', { className: 'btn', textContent: editable.label });
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

export function renderResult(host, { runId, detail }) {
  host.replaceChildren();

  if (!detail || !detail.stages?.length) {
    host.append(el('p', { className: 'empty', textContent: 'No output yet. Start a run.' }));
    return;
  }

  state.runDir = detail.dir;

  if (detail.running) {
    host.append(el('div', { className: 'banner' },
      'Running — output appears as each stage finishes.'));
  } else if (detail.stopped_at) {
    host.append(gateBanner(runId, detail));
  }

  for (const stage of detail.stages) {
    const mode = viewModes.get(stage.dir) || 'grid';
    const body = el('div', { className: 'stagebody' });

    const buttons = ['grid', 'anim', 'sheet'].map((m) => {
      const btn = el('button', {
        className: `segbtn ${mode === m ? 'on' : ''}`,
        textContent: { grid: 'Grid', anim: 'Animation', sheet: 'Sheet' }[m],
      });
      btn.onclick = () => {
        viewModes.set(stage.dir, m);
        renderResult(host, { runId, detail });
      };
      return btn;
    });

    const dl = el('button', { className: 'btn ghost', textContent: 'Download' });
    dl.onclick = () => downloadStage(runId, stage.name).catch((e) => toast(e.message, 'error'));

    body.append(
      mode === 'grid' ? grid(runId, stage)
      : mode === 'anim' ? animation(runId, stage)
      : sheet(runId, stage));

    host.append(el('section', { className: 'group stagesection' },
      el('h2', {},
        STAGE_LABEL[stage.name] || stage.name,
        el('span', { className: 'headnote', textContent: `${stage.dir} · ${stage.images.length} file(s)` }),
        el('span', { className: 'seg' }, ...buttons),
        dl),
      STAGE_NOTE[stage.name]
        ? el('p', { className: 'help stagehelp', textContent: STAGE_NOTE[stage.name] })
        : null,
      body));
  }

  const log = el('pre', { className: 'log', textContent: detail.log || '(no log)' });
  host.append(el('section', { className: 'group' },
    el('h2', { textContent: 'Log' }),
    el('div', { className: 'fields' }, log)));
  log.scrollTop = log.scrollHeight;
}
