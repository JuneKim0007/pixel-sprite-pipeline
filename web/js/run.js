/* Run tab — the guided flow, with the rig editor sitting inside it.
 *
 * Steps: Review → Rig → Check → Confirm. Pending edits live in the draft so
 * Back genuinely returns you to what you typed; nothing is written to disk
 * until you leave the Confirm step.
 *
 * The rig step is the reason gates exist. Editing skeletons is only useful
 * before the GPU stages consume them, so a configured `stop_after: pose` run
 * pauses there, you edit, and Resume continues with the edited files.
 */

import { api, getPath, setPath } from './api.js';
import { orderProblems } from './fields.js';
import { ROLES } from './input.js';
import { HelpTip } from './ui/index.js';
import { annotator } from './annotate.js';
import { rigEditor, savePoses } from './rig.js';
import { el } from './core/dom.js';
import { clearDraft, confirmDialog, draft, draftConfig, state, toast } from './store.js';

const STEPS = [
  { key: 'review', label: 'Review' },
  { key: 'rig', label: 'Rig editor' },
  { key: 'check', label: 'Check' },
  { key: 'confirm', label: 'Confirm' },
];

let rigDirty = false;

function stepper(onGo) {
  const bar = el('ol', { className: 'stepper' });
  STEPS.forEach((step, i) => {
    const node = el('li', {
      className: `step ${i === state.wizardStep ? 'on' : ''} ${i < state.wizardStep ? 'done' : ''}`,
    },
      el('span', { className: 'stepnum', textContent: String(i + 1) }),
      step.label);
    node.onclick = () => onGo(i);
    bar.append(node);
  });
  return bar;
}

/* ------------------------------------------------------------------ steps */

/* What the run will spend resting, before it is started rather than after.
 *
 * A pause that only appears in the log is one people discover by watching a
 * run seem to hang. Editable here too: the reason to rest is thermal, and
 * whoever is at the machine is the one who knows whether it matters today. */
function coolingLine(cfg, rerender) {
  const on = getPath(cfg, 'cooling.enabled') !== false;
  const secs = Number(getPath(cfg, 'cooling.seconds') ?? 180);
  const stages = getPath(cfg, 'pipeline.stages') || [];
  // `??` cannot sit beside `||` without parentheses - it is a syntax error,
  // not a precedence subtlety, and `node --check` accepts the file anyway.
  const frames = Number(getPath(cfg, 'pose.frames')
    ?? ((getPath(cfg, 'pose.set') || []).length || 1));
  const candidates = Number(getPath(cfg, 'canonical.candidates') ?? 1);
  const batched = getPath(cfg, 'canonical.batch_candidates') !== false;

  // Rests fall between GPU tasks: candidates inside the canonical (only when
  // they are sequential), then frames, and never after the last one.
  let tasks = 0;
  if (stages.includes('canonical')) tasks += batched ? 1 : candidates;
  if (stages.includes('frames')) tasks += frames;
  const total = on && secs > 0 ? Math.max(0, tasks - 1) * secs : 0;

  const box = el('div', { className: 'coolrow' });
  const value = el('input', {
    type: 'number', className: 'num', min: 0, max: 1800, step: 30,
    value: on ? secs : 0,
  });
  value.onchange = () => {
    const v = Math.max(0, Number(value.value));
    draft()['cooling.seconds'] = v;
    draft()['cooling.enabled'] = v > 0;
    rerender();
  };

  const tip = HelpTip(
    'Rest between GPU tasks. Nothing needs the pause to work; it is there so '
    + 'a long queue does not hold the machine at its throttle point all '
    + 'night. The minutes shown are added to the generation time.');

  box.append(
    el('div', { className: 'ui-label-row' },
      el('span', { className: 'key', textContent: 'Cooling' }), tip.btn),
    el('div', {},
      el('div', { className: 'row' },
        value, el('span', { className: 'mini', textContent: 's between GPU tasks' })),
      el('p', { className: 'mini', textContent: total
        ? `${tasks} GPU tasks · ${Math.round(total / 60)} min resting`
        : tasks > 1 ? 'off — continuous' : 'one task, nothing to rest between' }),
      tip.body));
  return box;
}


function reviewStep(rerender) {
  const cfg = draftConfig();
  const stages = getPath(cfg, 'pipeline.stages') || [];
  const gate = getPath(cfg, 'pipeline.stop_after') || '';
  const poseSource = getPath(cfg, 'pose.source') || 'library';
  const frames = getPath(cfg, 'pose.frames');
  // Per role, because "3 references" hides the thing worth checking before a
  // run: whether they are three identity images or three style exemplars.
  const refs = ROLES
    .map((r) => [r.label, (getPath(cfg, `references.${r.key}`) || []).length])
    .filter(([, n]) => n);

  const box = el('div', { className: 'group' },
    el('h2', { textContent: 'About to run' }),
    el('div', { className: 'fields' },
      summary('Subject', getPath(cfg, 'subject') || '(unset)'),
      summary('Stages', stages.join(' → ') || '(none)'),
      summary('Pose', `${poseSource}${frames ? ` · ${frames} frames` : ''} · ${getPath(cfg, 'pose.view') || 'side'}`),
      summary('References', refs.length
        ? refs.map(([label, n]) => `${n} ${label.toLowerCase()}`).join(' · ')
        : 'canonical sprite only'),
      summary('Palette', getPath(cfg, 'palette.source') || 'extract'),
      coolingLine(cfg, rerender)));

  /* Gate picker — where the flow should pause for review. */
  const gateSel = el('select', { className: 'select' });
  gateSel.append(el('option', { value: '', textContent: 'run straight through' }));
  for (const name of stages) {
    gateSel.append(el('option', { value: name, textContent: `stop after ${name}`, selected: gate === name }));
  }
  gateSel.onchange = () => {
    draft()['pipeline.stop_after'] = gateSel.value || null;
    rerender();
  };

  // Style chips: narrow a sheet's vocabulary for THIS run only. The sheet is
  // the durable decision; a chip is a one-off adjustment, so toggling one must
  // not write back to styles/.
  const applied = getPath(cfg, 'styles') || [];
  if (applied.length) {
    const chips = el('div', { className: 'stylechips' });
    const record = state.styleRecord || {};
    for (const [group, phrase] of Object.entries(record.vocabulary || {})) {
      if (!phrase) continue;
      for (const fragment of phrase.split(', ')) {
        const off = (draft()['_stylePicks']?.[group] || []).includes(fragment) === false
          && draft()['_stylePicks']?.[group];
        const chip = el('button', {
          className: `frag chip ${off ? '' : 'on'}`, textContent: fragment,
        });
        chip.onclick = () => {
          const picks = { ...(draft()['_stylePicks'] || {}) };
          const current = picks[group] || phrase.split(', ');
          picks[group] = current.includes(fragment)
            ? current.filter((f) => f !== fragment)
            : [...current, fragment];
          draft()['_stylePicks'] = picks;
          rerender();
        };
        chips.append(chip);
      }
    }
    if (chips.children.length) {
      box.querySelector('.fields').append(
        el('div', { className: 'field' },
          el('div', {},
            el('label', { textContent: 'Style vocabulary' }),
            el('div', { className: 'path', textContent: applied.join(' + ') })),
          el('p', { className: 'help', textContent:
            'Click to drop a fragment from this run. The style sheet is not '
            + 'changed — this is a one-off adjustment.' }),
          chips));
    }
  }

  box.querySelector('.fields').append(
    el('div', { className: 'field' },
      el('div', { className: 'field-top' },
        el('div', {},
          el('label', { textContent: 'Pause for review' }),
          el('div', { className: 'path', textContent: 'pipeline.stop_after' })),
        el('div', { className: 'control-wrap' }, gateSel)),
      el('p', { className: 'help', textContent:
        'Stopping after "pose" lets you fix skeletons before the GPU stages '
        + 'spend minutes on them. Resume continues from there.' })));

  return box;
}

function summary(label, value) {
  return el('div', { className: 'field compact' },
    el('div', { className: 'field-top' },
      el('label', { textContent: label }),
      el('span', { className: 'mono summaryval', textContent: String(value) })));
}

function rigStep() {
  const runId = state.selectedRun;
  const box = el('div', { className: 'group' });
  // Identity and pose references only. A palette swatch has no anatomy to
  // mark up and a style exemplar is not this character, so neither belongs in
  // an annotation picker. Matches annotate.gather() on the backend.
  const refs = ['identity', 'pose'].flatMap((role) =>
    (getPath(state.effective || {}, `references.${role}`) || [])
      .map((r) => ({ ...r, role })));

  // Two different jobs, so two modes rather than one confused editor:
  // authoring a pose you intend to generate, versus marking up an image that
  // already exists.
  const mode = state.rigMode || 'author';
  const seg = el('span', { className: 'seg' });
  for (const [key, label] of [['author', 'Author pose'], ['annotate', 'Annotate reference']]) {
    const btn = el('button', {
      className: `segbtn ${mode === key ? 'on' : ''}`, textContent: label,
      disabled: key === 'annotate' && !refs.length,
      title: key === 'annotate' && !refs.length ? 'Add a reference image first' : '',
    });
    btn.onclick = () => { state.rigMode = key; renderRun(document.querySelector('#view-run'), lastOpts); };
    seg.append(btn);
  }

  if (mode === 'annotate') {
    const picker = el('select', { className: 'select' });
    for (const ref of refs) {
      picker.append(el('option', { value: ref.path, textContent: ref.path.split('/').pop() }));
    }
    const holder = el('div', {});
    const load = () => holder.replaceChildren(
      annotator({ imagePath: picker.value, rigName: state.effective?.rig || 'humanoid' }));
    picker.onchange = load;

    box.append(
      el('h2', {}, 'Reference annotation', seg),
      el('div', { className: 'row' },
        el('span', { className: 'mini', textContent: 'Image' }), picker),
      holder);
    load();
    return box;
  }
  const save = el('button', { className: 'btn', textContent: 'Save pose guides', disabled: true });

  const editor = rigEditor({
    runId,
    onDirty: () => { rigDirty = true; save.disabled = false; },
  });

  save.onclick = async () => {
    try {
      if (await savePoses(runId)) {
        rigDirty = false;
        save.disabled = true;
      }
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  box.append(
    el('h2', {}, 'Rig editor', seg,
      el('span', { className: 'headnote', textContent: runId
        ? `editing ${runId}` : 'previewing the pose library — run the pose stage to edit' }),
      save),
    editor);
  return box;
}

function checkStep() {
  const cfg = draftConfig();
  const stages = getPath(cfg, 'pipeline.stages') || [];
  const problems = orderProblems(stages);
  const warnings = [];

  if (getPath(cfg, 'frames.lcm') && (getPath(cfg, 'frames.steps') ?? 20) < 12) {
    warnings.push('LCM with few steps ignores the pose skeleton — measured at 8 steps, '
      + 'the ControlNet cannot redirect composition. Use 20 steps for posed frames.');
  }
  if ((getPath(cfg, 'frames.controlnet.end_percent') ?? 0.8) < 0.3) {
    warnings.push('ControlNet end_percent below ~0.3 rarely establishes the pose at 20 steps.');
  }
  if (getPath(cfg, 'frames.cfg') > 3 && getPath(cfg, 'frames.lcm')) {
    warnings.push('LCM needs a low CFG (~1.5). Higher values burn the image.');
  }
  const gpuStages = stages.filter((s) =>
    state.schema.stages.find((x) => x.name === s)?.resource === 'gpu');
  const est = gpuStages.length
    ? `${gpuStages.length} GPU stage(s) — expect roughly 2 minutes for the canonical `
      + `plus ~3.5 minutes per frame at 20 steps.`
    : 'No GPU stages — this run is CPU only.';

  return el('div', { className: 'group' },
    el('h2', { textContent: 'Pre-flight' }),
    el('div', { className: 'fields' },
      problems.length
        ? el('div', { className: 'orderwarn' },
            el('b', { textContent: 'Stage order cannot run.' }),
            el('ul', {}, ...problems.map((p) => el('li', { textContent: p }))))
        : el('p', { className: 'ok', textContent: '✓ Stage order is valid.' }),
      ...warnings.map((w) => el('p', { className: 'warnline', textContent: `⚠ ${w}` })),
      el('p', { className: 'help', textContent: est })));
}

function confirmStep(onRun) {
  const cfg = draftConfig();
  const gate = getPath(cfg, 'pipeline.stop_after');
  const box = el('div', { className: 'group' },
    el('h2', { textContent: 'Ready' }),
    el('div', { className: 'fields' },
      el('p', { textContent: gate
        ? `This run will stop after "${gate}" so you can review it.`
        : 'This run will go through to the end.' })));

  const go = el('button', { className: 'btn primary lg', textContent: 'Run' });
  go.onclick = onRun;
  box.querySelector('.fields').append(el('div', { className: 'formfoot' }, go));
  return box;
}

/* ------------------------------------------------------------------- view */

let lastOpts = {};

export function renderRun(host, { onStarted, goTo }) {
  lastOpts = { onStarted, goTo };
  const rerender = () => renderRun(host, { onStarted, goTo });
  host.replaceChildren();

  const go = async (index) => {
    if (index > state.wizardStep && rigDirty && STEPS[state.wizardStep].key === 'rig') {
      const { ok } = await confirmDialog({
        title: 'Unsaved skeleton edits',
        body: '<p>You changed the rig but have not saved it. Leaving now discards those edits.</p>',
        confirmLabel: 'Discard and continue',
      });
      if (!ok) return;
      rigDirty = false;
    }
    state.wizardStep = Math.max(0, Math.min(STEPS.length - 1, index));
    rerender();
  };

  const startRun = async () => {
    const cfg = draftConfig();
    const gate = getPath(cfg, 'pipeline.stop_after');
    const suppressed = getPath(state.global, 'ui.suppress_gate_confirm');

    if (gate && !suppressed) {
      const { ok, remember } = await confirmDialog({
        title: 'This run will pause',
        body: `<p>Your settings stop the pipeline after <b>${gate}</b>, so the later `
            + `stages will not run yet.</p><p>You can review the output and continue `
            + `from the Run tab.</p>`,
        confirmLabel: 'Run anyway',
        rememberKey: 'gate',
      });
      if (!ok) return;
      if (remember) {
        setPath(state.global, 'ui.suppress_gate_confirm', true);
        api.saveGlobal(state.global).catch(() => {});
      }
    }

    try {
      // Read the underscore-prefixed draft entries first. They are run-scoped
      // rather than config-scoped, so committing the draft clears them — and
      // reading them afterwards silently sent every run without its style
      // picks.
      const picks = draft()['_stylePicks'];

      // Commit pending edits, then start.
      if (Object.keys(draft()).length) {
        const own = structuredClone(state.own);
        for (const [path, value] of Object.entries(draft())) {
          if (value === null || path.startsWith('_')) continue;
          setPath(own, path, value);
        }
        await api.saveConfig(state.current, { config: own });
        state.own = own;
        clearDraft();
      }
      const { run_id } = await api.start({
        config: state.current,
        ...(picks ? { style_picks: picks } : {}),
      });
      state.selectedRun = run_id;
      state.activeRun = run_id;
      toast(`Started ${run_id}`);
      onStarted?.(run_id);
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  host.append(stepper(go));

  const step = STEPS[state.wizardStep].key;
  host.append(
    step === 'review' ? reviewStep(rerender)
    : step === 'rig' ? rigStep()
    : step === 'check' ? checkStep()
    : confirmStep(startRun));

  const back = el('button', { className: 'btn ghost', textContent: '← Back', disabled: state.wizardStep === 0 });
  back.onclick = () => go(state.wizardStep - 1);
  const next = el('button', { className: 'btn', textContent: 'Next →' });
  next.onclick = () => go(state.wizardStep + 1);

  host.append(el('div', { className: 'wizardfoot' }, back,
    state.wizardStep < STEPS.length - 1 ? next : null));

  /* Resume banner for a gated run waiting to continue. */
  const stopped = state.runs.find((r) => r.id === state.selectedRun && r.stopped_at);
  if (stopped) {
    const resume = el('button', { className: 'btn primary', textContent: 'Resume run' });
    resume.onclick = async () => {
      try {
        await api.start({ resume: stopped.id });
        toast(`Resumed ${stopped.id}`);
        onStarted?.(stopped.id);
      } catch (e) {
        toast(e.message, 'error');
      }
    };
    host.prepend(el('div', { className: 'banner warn' },
      `${stopped.id} is paused after "${stopped.stopped_at}". `, resume));
  }
}
