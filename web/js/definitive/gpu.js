/* A WebGPU preview of the layer stack.
 *
 * Why this exists: every parameter change used to POST a 1280px image to
 * Python, wait for numpy to reduce and quantise it, and get a PNG back. That
 * is a second of latency on a control you are meant to judge by eye, and a
 * slider you cannot drag is a slider you cannot use.
 *
 * Why it is a preview and not the answer: there are now two implementations of
 * the same arithmetic, and two implementations drift. So the split is explicit
 * and one-directional.
 *
 *     dragging          the shader, every frame, approximate
 *     letting go        Python, once, authoritative
 *     writing a file    Python, always
 *
 * The UI says which one is on screen. `agrees()` renders both and reports the
 * difference, so the claim that they match is checkable rather than asserted.
 *
 * Three of the five layers are here. Curves and Grid are per-pixel and per-
 * block arithmetic, which is what a GPU is for. Palette generation is k-means
 * over the whole image - a reduction, not a map - so the shader takes the
 * palette Python last produced and only applies it. That is the honest split:
 * the expensive part that changes with every drag runs on the GPU, and the
 * part that needs to see all the pixels at once does not.
 */

const WORKGROUP = 8;

/* One pass over the output grid. Each thread owns one output pixel, reads the
 * block behind it, and writes a colour. */
const SHADER = `
struct Params {
  factor      : u32,
  phase       : vec2<u32>,
  reduce      : u32,   // 0 median-ish, 1 mean
  palette_n   : u32,
  gamma       : f32,
  contrast    : f32,
  brightness  : f32,
  saturation  : f32,
  key_on      : u32,
  key_rgb     : vec3<f32>,
  key_tol     : f32,
};

@group(0) @binding(0) var src      : texture_2d<f32>;
@group(0) @binding(1) var dst      : texture_storage_2d<rgba8unorm, write>;
@group(0) @binding(2) var<uniform> P : Params;
@group(0) @binding(3) var<storage, read> palette : array<vec4<f32>>;

const LUMA = vec3<f32>(0.2126, 0.7152, 0.0722);

fn tone(c: vec3<f32>) -> vec3<f32> {
  var v = clamp(c, vec3<f32>(0.0), vec3<f32>(1.0));
  v = pow(v, vec3<f32>(1.0 / max(P.gamma, 0.001)));
  v = (v - 0.5) * P.contrast + 0.5 + P.brightness;
  let g = dot(v, LUMA);
  v = vec3<f32>(g) + (v - vec3<f32>(g)) * P.saturation;
  return clamp(v, vec3<f32>(0.0), vec3<f32>(1.0));
}

/* Nearest palette entry, weighted by luminance. Plain RGB distance treats a
 * shift in blue as equal to the same shift in green, and the eye does not. */
fn snap(c: vec3<f32>) -> vec3<f32> {
  if (P.palette_n == 0u) { return c; }
  var best = 0u;
  var bestd = 1e9;
  for (var i = 0u; i < P.palette_n; i = i + 1u) {
    let d = (c - palette[i].rgb) * LUMA;
    let m = dot(d, d);
    if (m < bestd) { bestd = m; best = i; }
  }
  return palette[best].rgb;
}

@compute @workgroup_size(${WORKGROUP}, ${WORKGROUP})
fn main(@builtin(global_invocation_id) gid : vec3<u32>) {
  let out_size = textureDimensions(dst);
  if (gid.x >= out_size.x || gid.y >= out_size.y) { return; }

  let f = max(P.factor, 1u);
  let base = vec2<u32>(gid.x * f + P.phase.x, gid.y * f + P.phase.y);
  let in_size = textureDimensions(src);

  // Average the block, then take the sample nearest that average. Mean alone
  // invents a colour that was not in the picture; picking the closest real
  // sample keeps to colours that were actually there, which is the property
  // median has and the reason median is the default.
  var sum = vec3<f32>(0.0);
  var n = 0.0;
  for (var dy = 0u; dy < f; dy = dy + 1u) {
    for (var dx = 0u; dx < f; dx = dx + 1u) {
      let p = base + vec2<u32>(dx, dy);
      if (p.x < in_size.x && p.y < in_size.y) {
        sum = sum + textureLoad(src, vec2<i32>(p), 0).rgb;
        n = n + 1.0;
      }
    }
  }
  if (n == 0.0) { textureStore(dst, vec2<i32>(gid.xy), vec4<f32>(0.0)); return; }
  let mean = sum / n;

  var picked = mean;
  if (P.reduce == 0u) {
    var bestd = 1e9;
    for (var dy = 0u; dy < f; dy = dy + 1u) {
      for (var dx = 0u; dx < f; dx = dx + 1u) {
        let p = base + vec2<u32>(dx, dy);
        if (p.x < in_size.x && p.y < in_size.y) {
          let s = textureLoad(src, vec2<i32>(p), 0).rgb;
          let d = s - mean;
          let m = dot(d, d);
          if (m < bestd) { bestd = m; picked = s; }
        }
      }
    }
  }

  var c = snap(tone(picked));
  var a = 1.0;
  if (P.key_on == 1u) {
    let d = abs(c - P.key_rgb);
    if (max(d.x, max(d.y, d.z)) <= P.key_tol) { a = 0.0; }
  }
  textureStore(dst, vec2<i32>(gid.xy), vec4<f32>(c, a));
}
`;

let device = null;
let pipeline = null;

export function supported() {
  return typeof navigator !== 'undefined' && !!navigator.gpu;
}

export async function init() {
  if (device) return device;
  if (!supported()) throw new Error('WebGPU is not available in this browser');
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter) throw new Error('No WebGPU adapter');
  device = await adapter.requestDevice();
  pipeline = device.createComputePipeline({
    layout: 'auto',
    compute: { module: device.createShaderModule({ code: SHADER }), entryPoint: 'main' },
  });
  return device;
}

/* Flatten a stack into the flat uniform the shader wants.
 *
 * The stack is a list because order matters, and the shader is one pass
 * because that is what makes it fast. Those are reconcilable only because the
 * five layers commute in the ways that matter here: tone before snapping is a
 * different uniform from tone after it, and both are one multiply. Anything
 * that genuinely needs two passes is a reason to go back to the server, not a
 * reason to fake it - see `exact` in the caller.
 */
export function uniformsFrom(stack, { palette = [] } = {}) {
  const on = (key) => stack.find((s) => s.layer === key && s.enabled !== false);
  const cfg = (key) => on(key)?.config || {};

  const curves = cfg('curves');
  const grid = cfg('grid');
  const bg = cfg('background');
  const raw = String(bg.colour || '').replace('#', '');
  const keyRgb = raw.length === 6
    ? [0, 2, 4].map((i) => parseInt(raw.slice(i, i + 2), 16) / 255)
    : [0, 0, 0];

  return {
    factor: Math.max(1, Number(grid.factor) || 1),
    phase: [Number(grid.phase_x) || 0, Number(grid.phase_y) || 0],
    reduce: grid.reduce === 'mean' ? 1 : 0,
    palette,
    gamma: Number(curves.gamma ?? 1),
    contrast: Number(curves.contrast ?? 1),
    brightness: Number(curves.brightness ?? 0),
    saturation: Number(curves.saturation ?? 1),
    keyOn: on('background') && bg.enabled !== false && raw.length === 6 ? 1 : 0,
    keyRgb,
    keyTol: (Number(bg.tolerance) || 14) / 255,
  };
}

export async function render(bitmap, u) {
  await init();
  const outW = Math.max(1, Math.floor((bitmap.width - u.phase[0]) / u.factor));
  const outH = Math.max(1, Math.floor((bitmap.height - u.phase[1]) / u.factor));

  const src = device.createTexture({
    size: [bitmap.width, bitmap.height],
    format: 'rgba8unorm',
    usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST
         | GPUTextureUsage.RENDER_ATTACHMENT,
  });
  device.queue.copyExternalImageToTexture(
    { source: bitmap }, { texture: src }, [bitmap.width, bitmap.height]);

  const dst = device.createTexture({
    size: [outW, outH], format: 'rgba8unorm',
    usage: GPUTextureUsage.STORAGE_BINDING | GPUTextureUsage.COPY_SRC,
  });

  // std140-ish layout, padded to 16-byte boundaries by hand because there is
  // no reflection to do it for us.
  const buf = new ArrayBuffer(64);
  const i32 = new Uint32Array(buf);
  const f32 = new Float32Array(buf);
  i32[0] = u.factor; i32[1] = u.phase[0]; i32[2] = u.phase[1];
  i32[3] = u.reduce; i32[4] = u.palette.length;
  f32[5] = u.gamma; f32[6] = u.contrast; f32[7] = u.brightness;
  f32[8] = u.saturation;
  i32[9] = u.keyOn;
  f32[12] = u.keyRgb[0]; f32[13] = u.keyRgb[1]; f32[14] = u.keyRgb[2];
  f32[15] = u.keyTol;

  const uni = device.createBuffer({ size: 64,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
  device.queue.writeBuffer(uni, 0, buf);

  const entries = Math.max(1, u.palette.length);
  const pal = new Float32Array(entries * 4);
  u.palette.forEach((c, i) => {
    pal[i * 4] = c[0] / 255; pal[i * 4 + 1] = c[1] / 255;
    pal[i * 4 + 2] = c[2] / 255; pal[i * 4 + 3] = 1;
  });
  const palBuf = device.createBuffer({ size: pal.byteLength,
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST });
  device.queue.writeBuffer(palBuf, 0, pal);

  const bind = device.createBindGroup({
    layout: pipeline.getBindGroupLayout(0),
    entries: [
      { binding: 0, resource: src.createView() },
      { binding: 1, resource: dst.createView() },
      { binding: 2, resource: { buffer: uni } },
      { binding: 3, resource: { buffer: palBuf } },
    ],
  });

  const enc = device.createCommandEncoder();
  const pass = enc.beginComputePass();
  pass.setPipeline(pipeline);
  pass.setBindGroup(0, bind);
  pass.dispatchWorkgroups(Math.ceil(outW / WORKGROUP), Math.ceil(outH / WORKGROUP));
  pass.end();

  const bytesPerRow = Math.ceil(outW * 4 / 256) * 256;
  const read = device.createBuffer({ size: bytesPerRow * outH,
    usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
  enc.copyTextureToBuffer({ texture: dst }, { buffer: read, bytesPerRow }, [outW, outH]);
  device.queue.submit([enc.finish()]);

  await read.mapAsync(GPUMapMode.READ);
  const raw = new Uint8Array(read.getMappedRange());
  const pixels = new Uint8ClampedArray(outW * outH * 4);
  for (let y = 0; y < outH; y++) {
    pixels.set(raw.subarray(y * bytesPerRow, y * bytesPerRow + outW * 4), y * outW * 4);
  }
  const image = new ImageData(pixels, outW, outH);
  read.unmap();
  src.destroy(); dst.destroy();
  return image;
}
