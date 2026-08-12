/* The definitive edition: pixelisation you can see before you commit to it.
 *
 * Every control here already existed — as pipeline settings and as CLI flags
 * on pixelize.py. What was missing was the loop between changing one and
 * seeing what it did, which meant tuning a palette or a block size cost a
 * whole pipeline run per guess.
 *
 * The design borrows its shape from the small web converters (pixelit and its
 * relatives) and departs from them on one point that matters. Those tools give
 * you a block-size slider and let you hunt for the right value by eye. Block
 * size is not a preference — it is a property of the image, recoverable by
 * asking which factor reduces it without loss — and the same is true of the
 * grid's phase. So both are measured, shown as measurements, and only then
 * offered for override. A slider with no ruler beside it withholds an answer
 * we already have.
 *
 * Everything is server-side because the algorithms are: median-cut extraction,
 * the four colour metrics, multi-pass background keying and the phase search
 * all live in pipeline/pixelize.py, and reimplementing them in JavaScript
 * would create a second definition of "pixelise" that could drift from the one
 * the pipeline actually runs.
 */

import { api } from './api.js';
import { el, state, toast } from './store.js';

const REDUCE = [
  ['median', 'Median — robust to a stray bright pixel; the default'],
  ['mode', 'Mode — the most common exact colour. Best on already-posterised input'],
  ['mean', 'Mean — smooth, and the most likely to invent a colour that was not there'],
];

const MATCH = [
  ['weighted', 'Weighted — luminance-weighted RGB. Free, and fixes most of plain RGB'],
  ['luma', 'Luma — brightness first. Preserves the value ramp, which is what a sprite reads by'],
  ['lab', 'Lab — perceptually uniform. Most faithful, ~10x slower'],
  ['rgb', 'RGB — plain euclidean. What earlier outputs used; perceptually the worst'],
];

const params = {
  source: '', factor: 0, phase: 'auto', phase_x: 0, phase_y: 0,
  reduce: 'median', palette: '', colours: 0, match: 'weighted',
  dither: false, alpha_tolerance: 14, upscale: 4,
  curves: { brightness: 0, contrast: 1, gamma: 1, saturation: 1 },
};

let palettes = [];
let facts = null;
let busy = false;
let pending = false;

function row(label, control, hint) {
  return el('div', { className: 'field' },
    el('div', { className: 'field-top' },
      el('label', { textContent: label }),
      el('div', { className: 'control' }, control)),
    hint ? el('p', { className: 'help', textContent: hint }) : null);
}

function num(key, { min, max, step = 1, onChange }) {
  const input = el('input', { type: 'number', className: 'num',
                              value: params[key], min, max, step });
  input.onchange = () => { params[key] = Number(input.value); onChange(); };
  return input;
}

/* A curve control is a slider with its value beside it — these are judged by
 * eye against the preview, not typed. */
function curve(key, { min, max, step, onChange }) {
  const slider = el('input', { type: 'range', min, max, step,
                               value: params.curves[key] });
  const out = el('span', { className: 'val', textContent: params.curves[key].toFixed(2) });
  slider.oninput = () => { out.textContent = Number(slider.value).toFixed(2); };
  slider.onchange = () => { params.curves[key] = Number(slider.value); onChange(); };
  return el('div', { className: 'control' }, slider, out);
}

function pick(key, options, onChange) {
  const sel = el('select', { className: 'select' });
  for (const [value, label] of options) {
    sel.append(el('option', { value, textContent: label, selected: params[key] === value }));
  }
  sel.onchange = () => { params[key] = sel.value; onChange(); };
  return sel;
}

function swatches(colours) {
  const strip = el('div', { className: 'swatches' });
  for (const c of colours || []) {
    strip.append(el('span', { className: 'swatch', style: `background:${c}`, title: c }));
  }
  return strip;
}

function factsPanel() {
  if (!facts) return el('p', { className: 'mini', textContent: 'No preview yet.' });
  const b = facts.before, a = facts.after;
  const box = el('div', { className: 'factsgrid' });
  const add = (k, v, tone = '') =>
    box.append(el('div', { className: `fact ${tone}` },
      el('span', { className: 'mini', textContent: k }),
      el('b', { textContent: v })));

  add('measured block', `${facts.measured_block}px`, 'measured');
  add('using factor', `${facts.factor}`);
  add('grid phase', `${facts.phase[0]}, ${facts.phase[1]}`);
  add('size', `${b.width}×${b.height} → ${a.width}×${a.height}`);
  add('colours', `${b.colours.toLocaleString()} → ${a.colours}`);
  if (facts.palette_size) add('palette', `${facts.palette_size} entries`);
  return box;
}

export function renderEditor(host) {
  const rerender = () => renderEditor(host);
  host.replaceChildren();

  const before = el('div', { className: 'compare-cell' },
    el('h4', { textContent: 'Source' }),
    params.source
      ? el('img', { src: api.fileUrl(params.source), alt: 'source' })
      : el('p', { className: 'empty', textContent: 'Pick an image.' }));
  const after = el('div', { className: 'compare-cell' },
    el('h4', { textContent: 'Result' }),
    el('p', { className: 'empty', textContent: 'No preview yet.' }));
  const factsHost = el('div', { className: 'factshost' }, factsPanel());

  // One preview in flight at a time, with the last request replayed after it
  // lands. Dragging a slider otherwise queues a dozen full pixelisations of a
  // 1024px frame and the UI lags a second behind the control.
  async function preview() {
    if (!params.source) return;
    if (busy) { pending = true; return; }
    busy = true;
    try {
      const r = await api.editPreview(params);
      facts = r.facts;
      after.replaceChildren(
        el('h4', { textContent: 'Result' }),
        el('img', { src: r.image, alt: 'result', className: 'pixel' }));
      factsHost.replaceChildren(factsPanel());
    } catch (e) {
      after.replaceChildren(el('h4', { textContent: 'Result' }),
                            el('p', { className: 'warnline', textContent: e.message }));
    } finally {
      busy = false;
      if (pending) { pending = false; preview(); }
    }
  }

  /* -- source picking -------------------------------------------------- */

  const sourceSel = el('select', { className: 'select wide' });
  sourceSel.append(el('option', { value: '', textContent: '— pick an image —' }));
  sourceSel.onchange = () => {
    params.source = sourceSel.value;
    params.factor = 0;                 // re-measure for the new image
    rerender();
    preview();
  };

  const upload = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
  upload.onchange = async () => {
    if (!upload.files.length) return;
    try {
      const { saved } = await api.upload(upload.files);
      params.source = saved[0].path;
      params.factor = 0;
      rerender();
      preview();
    } catch (e) { toast(e.message, 'error'); }
  };
  const uploadBtn = el('button', { className: 'btn ghost', textContent: 'Upload…' });
  uploadBtn.onclick = () => upload.click();

  /* -- controls -------------------------------------------------------- */

  const controls = el('div', { className: 'group' },
    el('h2', { textContent: 'Grid' }),
    el('div', { className: 'fields' },
      row('Block size',
        el('div', { className: 'control' },
          num('factor', { min: 0, max: 64, onChange: preview }),
          (() => {
            const b = el('button', { className: 'pill', textContent: 'measure' });
            b.onclick = () => { params.factor = 0; preview(); };
            return b;
          })()),
        'Zero measures it from the image: the largest factor that reduces '
        + 'without loss. Override only if the measurement is visibly wrong.'),
      row('Grid origin',
        pick('phase', [['auto', 'Auto — minimum intra-block variance'],
                       ['manual', 'Manual']], preview),
        'Where the pixel lattice starts. Sampling on the wrong phase straddles '
        + 'block boundaries and smears two logical pixels into one.'),
      params.phase === 'manual'
        ? row('Origin x, y', el('div', { className: 'control' },
            num('phase_x', { min: 0, max: 63, onChange: preview }),
            num('phase_y', { min: 0, max: 63, onChange: preview })), null)
        : null,
      row('Block reduce', pick('reduce', REDUCE, preview),
        'How the pixels inside one block collapse to a single colour.')));

  const paletteSel = el('select', { className: 'select wide' });
  paletteSel.append(el('option', { value: '', textContent: '— none (keep colours) —' }));
  for (const p of palettes) {
    paletteSel.append(el('option', { value: p.name,
      textContent: `${p.name} (${p.size})`, selected: params.palette === p.name }));
  }
  paletteSel.onchange = () => { params.palette = paletteSel.value; rerender(); preview(); };

  const chosen = palettes.find((p) => p.name === params.palette);
  const ditherBox = el('input', { type: 'checkbox', checked: params.dither });
  ditherBox.onchange = () => { params.dither = ditherBox.checked; preview(); };

  const colour = el('div', { className: 'group' },
    el('h2', { textContent: 'Colour' }),
    el('div', { className: 'fields' },
      row('Palette', paletteSel,
        'A fixed palette is what makes colour exact across an animation — every '
        + 'frame snaps to the same set instead of each landing somewhere near it.'),
      chosen ? el('div', { className: 'field' }, swatches(chosen.colours)) : null,
      params.palette ? null : row('Generate colours',
        num('colours', { min: 0, max: 256, onChange: preview }),
        'With no palette chosen, cluster this image into N colours using the '
        + 'metric below. Not median cut: on a generated knight, median cut put '
        + 'five of eight entries within four luminance steps of each other — a '
        + 'palette with no value range, for a medium that reads by value. '
        + 'Zero leaves the colours alone.'),
      row('Matching', pick('match', MATCH, preview),
        'How "nearest colour" is decided. Irrelevant when colours are '
        + 'extracted from this same image, since they already fit.'),
      row('Dither', ditherBox,
        'Trades flat blocks for apparent depth. Off suits a chunky idiom; on '
        + 'when a small palette has to carry a gradient.')));

  const reset = el('button', { className: 'pill', textContent: 'reset' });
  reset.onclick = () => {
    params.curves = { brightness: 0, contrast: 1, gamma: 1, saturation: 1 };
    rerender(); preview();
  };
  const tone = el('div', { className: 'group' },
    el('h2', {}, 'Curves', reset),
    el('div', { className: 'fields' },
      row('Gamma', curve('gamma', { min: 0.4, max: 2.5, step: 0.05, onChange: preview }),
        'Redistributes within the range rather than shifting it — what '
        + '"shadows too dark, highlights fine" actually needs.'),
      row('Contrast', curve('contrast', { min: 0.4, max: 2.5, step: 0.05, onChange: preview })),
      row('Brightness', curve('brightness', { min: -0.4, max: 0.4, step: 0.02, onChange: preview })),
      row('Saturation', curve('saturation', { min: 0, max: 2.5, step: 0.05, onChange: preview }))));

  const output = el('div', { className: 'group' },
    el('h2', { textContent: 'Output' }),
    el('div', { className: 'fields' },
      row('Background tolerance',
        num('alpha_tolerance', { min: 0, max: 64, onChange: preview }),
        'Colour distance from the corner colour that still counts as '
        + 'background. Raise it when a two-tone backdrop survives; lower it '
        + 'when the sprite starts losing its own dark edges.'),
      row('Preview zoom', num('upscale', { min: 1, max: 16, onChange: preview }),
        'Nearest-neighbour, so it magnifies without inventing anything. '
        + 'Applied to the written file too.')));

  const apply = el('button', { className: 'btn primary', textContent: 'Write _px.png',
                               disabled: !params.source });
  apply.onclick = async () => {
    try {
      const r = await api.editApply(params);
      toast(`Wrote ${r.written.split('/').pop()}`);
    } catch (e) { toast(e.message, 'error'); }
  };

  host.append(
    el('header', { className: 'head' },
      el('div', {},
        el('h1', { textContent: 'Editor' }),
),
      el('div', { className: 'head-actions' }, uploadBtn, upload, apply)),
    el('div', { className: 'row' },
      el('span', { className: 'mini', textContent: 'Source' }), sourceSel),
    el('div', { className: 'editorbody' },
      el('div', {},
        el('div', { className: 'compare' }, before, after),
        factsHost),
      el('div', { className: 'editorside' }, controls, tone, colour, output)));

  /* -- populate the pickers -------------------------------------------- */

  (async () => {
    if (!palettes.length) {
      try { palettes = (await api.palettes()).palettes; rerender(); return; }
      catch { /* the palette list is optional */ }
    }
    try {
      const { runs } = await api.runs();
      for (const run of runs.slice(0, 12)) {
        for (const stage of run.stages) {
          for (const image of stage.images) {
            const path = `${state.system?.paths?.output_dir || 'out/runs'}/${run.id}/${stage.dir}/${image}`;
            sourceSel.append(el('option', { value: path,
              textContent: `${run.id} · ${stage.name} · ${image}`,
              selected: params.source === path }));
          }
        }
      }
    } catch { /* runs are optional too */ }
  })();
}
