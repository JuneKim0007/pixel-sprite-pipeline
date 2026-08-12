/* The definitive editor: an ordered stack of layers over one image.
 *
 * The chain used to be eight fixed steps and a form written by hand beside
 * each control. Being right about the order is not the same as being able to
 * express one, and where a step sits changes what it does: curves before the
 * palette decide which entries get picked, curves after it just move colours
 * off the palette again.
 *
 * So nothing here knows what a layer does or what fields it has. It fetches a
 * catalogue, renders whatever that describes, and posts the stack back. Adding
 * a layer is adding one to pipeline/definitive/; this file does not change,
 * and neither does the guarantee that every control carries its (?), because
 * the form is built by BaseField rather than by whoever adds the control.
 *
 * Two engines draw the same picture, deliberately:
 *
 *   dragging a slider   WebGPU, every frame, approximate
 *   letting go          Python, once, authoritative
 *   writing a file      Python, always
 *
 * A round trip per keystroke was a second of latency on controls meant to be
 * judged by eye, and a slider you cannot drag is a slider you cannot use. The
 * cost is two implementations of the same arithmetic, which is a real cost, so
 * which one is on screen is labelled rather than hidden and Python decides
 * anything that gets saved.
 */

import { api } from './api.js';
import { el, state, toast } from './store.js';
import { layerForm, stackList } from './definitive/stack.js';
import * as gpu from './definitive/gpu.js';

let catalogue = [];
let stack = [];
let selected = null;
let source = '';
let facts = null;
let engine = 'exact';
let bitmap = null;      // the source decoded once, for the shader
let palette = [];       // whatever Python last produced, so the shader matches it
let busy = false, pending = false, exactTimer = null;

/* ------------------------------------------------------------------ facts */

function factsBar() {
  if (!facts) return el('p', { className: 'mini', textContent: 'No preview yet.' });
  const b = facts.before, a = facts.after;
  const box = el('div', { className: 'factsgrid' });
  const add = (k, v, tone = '') => box.append(
    el('div', { className: `fact ${tone}` },
      el('span', { className: 'mini', textContent: k }),
      el('b', { textContent: v })));

  if (facts.measured_block !== undefined) {
    add('measured block', `${facts.measured_block}px`, 'measured');
    add('using', String(facts.factor));
    add('phase', (facts.phase || []).join(', '));
  }
  if (b && a) {
    add('size', `${b.width}x${b.height} to ${a.width}x${a.height}`);
    add('colours', `${b.colours.toLocaleString()} to ${a.colours}`);
  }
  if (facts.palette_size) add('palette', `${facts.palette_size} entries`);
  if (facts.kept !== undefined) add('subject', `${Math.round(facts.kept * 100)}%`);

  const host = el('div', {}, box);
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
  const rerender = () => renderEditor(host);
  host.replaceChildren();

  const after = el('div', { className: 'compare-cell' });
  const factsHost = el('div', { className: 'factshost' }, factsBar());

  const head = (label) => el('h4', {}, 'Result',
    el('span', { className: `enginetag ${engine}`, textContent: label }));

  /* The fast path. Approximate, and labelled as such. */
  async function drawPreview() {
    if (!bitmap || !gpu.supported()) return false;
    try {
      const image = await gpu.render(bitmap, gpu.uniformsFrom(stack, { palette }));
      engine = 'preview';
      const canvas = el('canvas', { width: image.width, height: image.height,
                                    className: 'pixel previewcanvas' });
      canvas.getContext('2d').putImageData(image, 0, 0);
      after.replaceChildren(head('preview'), canvas);
      return true;
    } catch (e) {
      toast(`WebGPU preview unavailable: ${e.message}`, 'error');
      return false;
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
      after.replaceChildren(head('exact'), el('img', { src: r.image, className: 'pixel' }));
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

  /* Read the colours Python settled on, so the shader approximates the same
   * picture rather than a different one. Generating a palette is k-means over
   * every pixel, which is a reduction and not a map; the GPU applies one but
   * does not derive one. */
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

  /* A change draws on the GPU now and settles to Python shortly after.
   * Debounced, because letting go of one slider usually means grabbing the
   * next one. */
  const changed = async () => {
    const drew = await drawPreview();
    clearTimeout(exactTimer);
    exactTimer = setTimeout(drawExact, drew ? 400 : 0);
  };

  /* ---------------------------------------------------------- the source */

  const sourceSel = el('select', { className: 'select wide' });
  sourceSel.append(el('option', { value: '', textContent: 'pick an image' }));
  sourceSel.onchange = async () => {
    source = sourceSel.value;
    bitmap = null;
    if (source) {
      try {
        const res = await fetch(api.fileUrl(source));
        bitmap = await createImageBitmap(await res.blob());
      } catch { bitmap = null; }
    }
    // A different image has a different block size, so let Grid measure again.
    const grid = stack.find((s) => s.layer === 'grid');
    if (grid) grid.config.factor = 0;
    rerender();
    drawExact();
  };

  const upload = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
  upload.onchange = async () => {
    if (!upload.files.length) return;
    try {
      const { saved } = await api.upload(upload.files);
      source = saved[0].path;
      const res = await fetch(api.fileUrl(source));
      bitmap = await createImageBitmap(await res.blob());
      rerender();
      drawExact();
    } catch (e) { toast(e.message, 'error'); }
  };
  const uploadBtn = el('button', { className: 'btn ghost', textContent: 'Upload' });
  uploadBtn.onclick = () => upload.click();

  const apply = el('button', { className: 'btn primary', textContent: 'Write _px.png',
                               disabled: !source });
  apply.onclick = async () => {
    try {
      const r = await api.editApply({ source, stack });
      toast(`Wrote ${r.written.split('/').pop()}`);
    } catch (e) { toast(e.message, 'error'); }
  };

  /* ----------------------------------------------------------- the stack */

  const listHost = el('div', { className: 'stackpanel' });
  const formHost = el('div', { className: 'stackform' });
  const redraw = () => { renderStack(); changed(); };

  function renderStack() {
    listHost.replaceChildren(
      el('div', { className: 'ovhead' },
        el('h2', { textContent: 'Layers' }),
        el('span', { className: 'mini', textContent: 'drag to reorder' })),
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
        el('p', { className: 'empty', textContent: 'Pick a layer to configure it.' }));
      return;
    }
    const spec = catalogue.find((s) => s.key === entry.layer);
    formHost.replaceChildren(
      el('div', { className: 'ovhead' },
        el('h2', { textContent: spec.label }),
        el('span', { className: 'mini', textContent: spec.summary })),
      layerForm(spec, entry.config, (key, value) => {
        entry.config[key] = value;
        // A field can reveal another, so the form rebuilds on every change.
        renderForm();
        changed();
      }));
  }

  host.append(
    el('header', { className: 'head' },
      el('div', {}, el('h1', { textContent: 'Editor' })),
      el('div', { className: 'head-actions' }, uploadBtn, upload, apply)),
    el('div', { className: 'row' },
      el('span', { className: 'mini', textContent: 'Source' }), sourceSel),
    el('div', { className: 'editorbody' },
      el('div', {},
        el('div', { className: 'compare' },
          el('div', { className: 'compare-cell' },
            el('h4', { textContent: 'Source' }),
            source ? el('img', { src: api.fileUrl(source), alt: 'source' })
                   : el('p', { className: 'empty', textContent: 'Pick an image.' })),
          after),
        factsHost),
      el('div', { className: 'editorside' }, listHost, formHost)));

  after.replaceChildren(el('h4', { textContent: 'Result' }),
                        el('p', { className: 'empty', textContent: 'No preview yet.' }));

  (async () => {
    if (!catalogue.length) {
      try {
        const d = await api.editorLayers();
        catalogue = d.layers;
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

    if (source) drawExact();
  })();
}
