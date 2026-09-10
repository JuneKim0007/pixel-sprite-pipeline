// The definitive editor: an ordered stack of layers over one image.

import { api } from '../../api.js';
import { showError } from '../../core/errors.js';
import { el } from '../../core/dom.js';
import { state, toast } from '../../store.js';
import { layerForm, stackList } from './stack.js';
import { clampFraction, fractionAt, loadRatios, saveRatios, splitter } from './panes.js';
import { lightbox } from '../../ui/dialog.js';
import { Button, Empty, Fact, FactGrid, PanelHead } from '../../ui/index.js';
import * as gpu from './gpu.js';

let catalogue = [];
let stack = [];
let selected = null;
let source = '';
let facts = null;
let engine = 'exact';
let bitmap = null;      // the source decoded once, for the shader
let palette = [];       // whatever Python last produced, so the shader matches it
let busy = false, pending = false;
let previewEdge = 384;      // replaced by the server's budget on first load
let drawing = false, queued = false;

// Decode at the preview budget, not at the file's size.
async function decode(path) {
  const blob = await (await fetch(api.fileUrl(path))).blob();
  const probe = await createImageBitmap(blob);
  const longest = Math.max(probe.width, probe.height);
  if (longest <= previewEdge) return probe;
  const scale = previewEdge / longest;
  probe.close();
  return createImageBitmap(blob, {
    resizeWidth: Math.max(1, Math.round(probe.width * scale)),
    resizeHeight: Math.max(1, Math.round(probe.height * scale)),
    resizeQuality: 'pixelated',
  });
}

/* ------------------------------------------------------------------ facts */

function factsBar() {
  if (!facts) return el('p', { className: 'mini', textContent: 'No preview yet.' });
  const b = facts.before, a = facts.after;
  const rows = [];
  const add = (k, v, tone = '') => rows.push(Fact(k, v, tone));

  if (facts.measured_block !== undefined) {
    add('measured block', `${facts.measured_block}px`, 'measured');
    add('using', String(facts.factor));
    add('phase', (facts.phase || []).join(', '));
  }
  if (b && a) {
    const d = facts.deferred;
    const shown = d && d.scale > 1 ? `${d.width}x${d.height}` : `${a.width}x${a.height}`;
    add('size', `${b.width}x${b.height} to ${shown}`);
    add('colours', `${b.colours.toLocaleString()} to ${a.colours}`);
    if (d && d.scale > 1) add('magnified by', `${d.scale}x on display`, 'measured');
  }
  if (facts.palette_size) add('palette', `${facts.palette_size} entries`);
  if (facts.kept !== undefined) add('subject', `${Math.round(facts.kept * 100)}%`);

  const host = el('div', {}, FactGrid(...rows));
  for (const w of facts.warnings || []) {
    host.append(el('p', { className: 'warnline', textContent: `! ${w}` }));
  }
  for (const layer of facts.layers || []) {
    if (layer.error) {
      host.append(el('p', { className: 'warnline',
                            textContent: `! ${layer.layer}: ${layer.error}` }));
    }
  }
  return host;
}

/* ------------------------------------------------------------------- view */

export function renderEditor(host) {
  host.replaceChildren();

  const after = el('div', { className: 'pane' });
  const sourceCell = el('div', {});

  const drawSource = () => sourceCell.replaceChildren(
    source ? el('div', { className: 'compare-stage' },
                el('img', { src: api.fileUrl(source), alt: 'source' }))
           : Empty('Pick an image.'));
  const factsHost = el('div', { className: 'factshost' }, factsBar());

  const head = (label, size) => {
    const node = el('h4', {}, 'Result',
      el('span', { className: `enginetag ${engine}`, textContent: label }));
    if (size) node.append(el('span', { className: 'pixsize', textContent: size }));
    return node;
  };

  const showResult = (label, node, size) => after.replaceChildren(
    head(label, size), el('div', { className: 'compare-stage' }, node));

  const sizeLabel = (w, h) => `${w}×${h}`;

  let toldReason = '';
  function explainNoPreview() {
    const reason = !gpu.supported()
      ? 'Live preview needs WebGPU, which this browser does not offer.'
      : 'That image could not be decoded for the live preview.';
    if (reason === toldReason) return;
    toldReason = reason;
    toast(`${reason} Generate preview still works.`, 'warn');
  }

  async function useSource(path) {
    source = path;
    bitmap = null;
    toldReason = '';
    const grid = stack.find((s) => s.layer === 'grid');
    if (grid) grid.config.factor = 0;
    if (!path) return;
    try {
      bitmap = await decode(path);
    } catch (e) {
      toast(`Could not decode ${path.split('/').pop()}: ${e.message}`, 'warn');
    }
  }

  /* The fast path. Approximate, and labelled as such. */
  async function drawPreview() {
    if (!gpu.supported() || !bitmap) { explainNoPreview(); return false; }
    // One at a time.
    if (drawing) { queued = true; return false; }
    drawing = true;
    try {
      const image = await gpu.render(bitmap, gpu.uniformsFrom(stack, { palette }));
      engine = 'preview';
      const canvas = el('canvas', { width: image.width, height: image.height,
                                    className: 'pixel previewcanvas' });
      canvas.getContext('2d').putImageData(image, 0, 0);
      showResult('preview', canvas, sizeLabel(image.width, image.height));
      return true;
    } catch (e) {
      toast(`WebGPU preview unavailable: ${e.message}`, 'error');
      return false;
    } finally {
      drawing = false;
      if (queued) { queued = false; drawPreview(); }
    }
  }

  /* The authoritative path. One in flight; the last request replays after. */
  async function drawExact() {
    if (!source) return;
    if (busy) { pending = true; return; }
    busy = true;
    try {
      const r = await api.editPreview({ source, stack });
      facts = r.facts;
      engine = 'exact';

      const img = el('img', { src: r.image, className: 'pixel' });
      img.onclick = () => lightbox(r.image, `${source.split('/').pop()} · ${engine}`);
      const a = r.facts?.after;
      const d = r.facts?.deferred;
      let size = a ? sizeLabel(a.width, a.height) : '';
      if (size && d && d.scale > 1) size += ` → ${sizeLabel(d.width, d.height)}`;

      showResult('exact', img, size);
      factsHost.replaceChildren(factsBar());
      palette = [];      // refreshed from the rendered image below
      samplePalette(r.image);
    } catch (e) {
      after.replaceChildren(el('h4', { textContent: 'Result' }),
                            el('p', { className: 'warnline', textContent: e.message }));
    } finally {
      busy = false;
      if (pending) { pending = false; drawExact(); }
    }
  }

  function samplePalette(dataUrl) {
    const img = new Image();
    img.onload = () => {
      try {
        const c = el('canvas', { width: img.width, height: img.height });
        c.getContext('2d').drawImage(img, 0, 0);
        const d = c.getContext('2d').getImageData(0, 0, img.width, img.height).data;
        const seen = new Set();
        for (let i = 0; i < d.length; i += 4) {
          if (d[i + 3] === 0) continue;
          seen.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
          if (seen.size > 256) break;
        }
        palette = [...seen].map((v) => [(v >> 16) & 255, (v >> 8) & 255, v & 255]);
      } catch { palette = []; }
    };
    img.src = dataUrl;
  }

  // Nothing runs on its own.
  const generate = Button('Generate preview', { variant: 'primary' });

  const markStale = () => {
    generate.classList.add('wants');
    drawPreview();      // a frame of GPU work, so it can stay live
  };

  generate.onclick = async () => {
    generate.disabled = true;
    generate.textContent = 'working…';
    try {
      await drawExact();
      generate.classList.remove('wants');
    } finally {
      generate.disabled = false;
      generate.textContent = 'Generate preview';
    }
  };

  /* ---------------------------------------------------------- the source */

  const sourceSel = el('select', { className: 'select wide' });
  sourceSel.append(el('option', { value: '', textContent: 'pick an image' }));
  sourceSel.onchange = async () => {
    await useSource(sourceSel.value);
    drawSource();
    renderForm();
    markStale();
  };

  const upload = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
  upload.onchange = async () => {
    if (!upload.files.length) return;
    try {
      const { saved } = await api.upload(upload.files);
      await useSource(saved[0].path);
      sourceSel.append(el('option', { value: source, textContent: source.split('/').pop(),
                                      selected: true }));
      drawSource();
      renderForm();
      markStale();
    } catch (e) { showError(e); }
  };
  const uploadBtn = Button('Upload', { variant: 'ghost' });
  uploadBtn.onclick = () => upload.click();

  const apply = Button('Write _px.png', { variant: 'primary', disabled: !source });
  apply.onclick = async () => {
    try {
      const r = await api.editApply({ source, stack });
      toast(`Wrote ${r.written.split('/').pop()} at ${r.width}x${r.height}`);
    } catch (e) { showError(e); }
  };

  /* ----------------------------------------------------------- the stack */

  const listHost = el('div', { className: 'stackpanel' });
  const formHost = el('div', { className: 'stackform' });
  const redraw = () => { renderStack(); markStale(); };

  function renderStack() {
    listHost.replaceChildren(
      PanelHead('Layers', { note: 'drag to reorder' }),
      stackList(stack, catalogue, {
        selected,
        onSelect: (id) => { selected = id; renderForm(); },
        onToggle: (i) => { stack[i].enabled = stack[i].enabled === false; redraw(); },
        onRemove: (i) => {
          if (stack[i].id === selected) selected = null;
          stack.splice(i, 1);
          redraw();
        },
        onReorder: (from, to) => {
          const [moved] = stack.splice(from, 1);
          stack.splice(to, 0, moved);
          redraw();
        },
        onAdd: (key) => {
          const spec = catalogue.find((s) => s.key === key);
          const config = Object.fromEntries(spec.fields.map((f) => [f.key, f.default]));
          const id = `${key}${Date.now().toString(36)}`;
          stack.push({ layer: key, id, enabled: true, config });
          selected = id;
          redraw();
        },
      }));
    renderForm();
  }

  function renderForm() {
    const entry = stack.find((s) => s.id === selected);
    if (!entry) {
      formHost.replaceChildren(
        Empty('Pick a layer to configure it.'));
      return;
    }
    const spec = catalogue.find((s) => s.key === entry.layer);
    formHost.replaceChildren(
      PanelHead(spec.label, { note: spec.summary }),
      layerForm(spec, entry.config, (key, value) => {
        entry.config[key] = value;
        // Only rebuild when this key gates another field's visibility.
        if (spec.fields.some((f) => key in (f.when || {}))) renderForm();
        markStale();
      }, (key) => {
        const field = spec.fields.find((f) => f.key === key);
        entry.config[key] = field?.default;
        renderForm();
        markStale();
      }));
  }

  drawSource();

  const ratios = loadRatios({ side: 0.72, compare: 0.5 });
  const body = el('div', { className: 'editorbody' });
  const work = el('div', { className: 'editorwork' });
  const compare = el('div', { className: 'compare' });
  const side = el('div', { className: 'editorside' }, listHost, formHost);

  const applyRatios = () => {
    body.style.setProperty('--side-split', `${ratios.side * 100}%`);
    compare.style.setProperty('--compare-split', `${ratios.compare * 100}%`);
  };

  const dragRatio = (key, host, axis) => (e) => {
    if (e.nudge !== undefined) {
      ratios[key] = clampFraction(ratios[key] + e.nudge);
    } else {
      const box = host.getBoundingClientRect();
      const along = axis === 'x' ? e.clientX - box.left : e.clientY - box.top;
      ratios[key] = fractionAt(along, axis === 'x' ? box.width : box.height);
    }
    applyRatios();
  };

  compare.append(
    el('div', { className: 'pane' },
      el('h4', { textContent: 'Source' }), sourceCell),
    splitter('x', {
      label: 'Drag to resize source and result',
      onMove: dragRatio('compare', compare, 'x'),
      onDrop: () => saveRatios(ratios),
    }),
    after);

  work.append(compare, factsHost);
  body.append(
    work,
    splitter('x', {
      label: 'Drag to resize the work area and the layer panel',
      onMove: dragRatio('side', body, 'x'),
      onDrop: () => saveRatios(ratios),
    }),
    side);
  applyRatios();

  host.append(
    el('header', { className: 'head' },
      el('div', {}, el('h1', { textContent: 'Editor' })),
      el('div', { className: 'head-actions' }, uploadBtn, upload, generate, apply)),
    el('div', { className: 'row' },
      el('span', { className: 'mini', textContent: 'Source' }), sourceSel),
    body);

  after.replaceChildren(el('h4', { textContent: 'Result' }),
                        Empty('No preview yet.'));

  (async () => {
    if (!catalogue.length) {
      try {
        const d = await api.editorLayers();
        catalogue = d.layers;
        previewEdge = d.limits?.preview_edge || previewEdge;
        if (!stack.length) stack = d.default_stack;
        selected = selected || stack[0]?.id || null;
      } catch (e) {
        listHost.replaceChildren(el('p', { className: 'warnline', textContent: e.message }));
        return;
      }
    }
    renderStack();

    try {
      const { runs } = await api.runs();
      const base = state.system?.paths?.output_dir || 'out/runs';
      for (const run of runs.slice(0, 12)) {
        for (const stage of run.stages) {
          for (const image of stage.images) {
            const path = `${base}/${run.id}/${stage.dir}/${image}`;
            sourceSel.append(el('option', { value: path, selected: path === source,
              textContent: `${run.id} · ${stage.name} · ${image}` }));
          }
        }
      }
    } catch { /* the picker is a convenience, not a requirement */ }

    if (source && !bitmap) await useSource(source);
    if (source) markStale();
  })();
}
