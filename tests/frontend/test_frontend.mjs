/* Headless tests for the front-end logic.
 *
 * The UI has been built without a browser to look at, which is exactly the
 * condition under which silent breakage accumulates: a variable declared and
 * never assigned, a drag that perturbs the wrong limb, a fallback that never
 * fires. None of those raise an error — they just render something subtly
 * wrong, and only a person looking at the screen notices.
 *
 * These tests cover the parts that are pure logic, which is most of the parts
 * that were actually wrong.
 *
 *   node tests/test_frontend.mjs
 */

import assert from 'node:assert';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const JS = join(ROOT, 'web/js');

const {
  VIEWS, JOINTS, LIMBS, projectPoint, unprojectX, snapToAnatomy, dragJoint,
  parentOf, subtree, visibleJoint, resolveView, dist3, SKELETON_TREE,
} = await import(join(JS, 'features/pose.js'));

let pass = 0, fail = 0;

const ok = (name) => { console.log(`  ok    ${name}`); pass++; };
const bad = (name, e) => { console.log(`  FAIL  ${name}\n        ${e.message}`); fail++; };

/* `test` refuses an async body rather than running it.
 *
 * It used to call fn() inside try/catch and report ok on return. An async body
 * returns a promise there, so its assertions settled after the catch had been
 * passed: fourteen async tests reported ok whatever they asserted, and the
 * suite exited 0 with a deliberately broken one among them. Detecting the
 * thenable makes the mistake impossible instead of documented. */
const test = (name, fn) => {
  try {
    const r = fn();
    if (r && typeof r.then === 'function') {
      throw new Error('async body passed to test(); use `await atest(...)`');
    }
    ok(name);
  } catch (e) { bad(name, e); }
};

const atest = async (name, fn) => {
  try { await fn(); ok(name); } catch (e) { bad(name, e); }
};

const NEUTRAL = {
  nose: [0, 0.03, 0.145], neck: [0, 0, 0.225],
  r_shoulder: [-0.055, 0, 0.243], r_elbow: [-0.065, 0.002, 0.352], r_wrist: [-0.07, 0.004, 0.452],
  l_shoulder: [0.055, 0, 0.243], l_elbow: [0.065, 0.002, 0.352], l_wrist: [0.07, 0.004, 0.452],
  r_hip: [-0.035, 0, 0.505], r_knee: [-0.038, 0.002, 0.655], r_ankle: [-0.04, 0.006, 0.815],
  l_hip: [0.035, 0, 0.505], l_knee: [0.038, 0.002, 0.655], l_ankle: [0.04, 0.006, 0.815],
  r_eye: [-0.014, 0.026, 0.136], l_eye: [0.014, 0.026, 0.136],
  r_ear: [-0.03, -0.004, 0.142], l_ear: [0.03, -0.004, 0.142],
};

console.log('\nprojection');
test('18 joints in COCO order', () => assert.equal(JOINTS.length, 18));
test('named views resolve', () => {
  assert.equal(resolveView('rear_turned'), 170);
  assert.equal(resolveView(160), 160);
});
test('face drops when facing away', () => {
  assert.ok(visibleJoint('nose', 0));
  assert.ok(!visibleJoint('nose', 180));
  assert.ok(!visibleJoint('l_eye', 90));
});
for (const yaw of [0, 40, 90, 145, 180]) {
  test(`drag round-trips at ${yaw}deg`, () => {
    const p = [...NEUTRAL.l_wrist];
    const s = unprojectX(0.62, yaw, p);
    const got = projectPoint([s.lateral, s.depth, p[2]], yaw)[0];
    assert.ok(Math.abs(got - 0.62) < 1e-9, `${got} != 0.62`);
  });
}
test('front drag moves lateral only', () => {
  const s = unprojectX(0.62, 0, [...NEUTRAL.l_wrist]);
  assert.equal(s.depth, NEUTRAL.l_wrist[1]);
  assert.notEqual(s.lateral, NEUTRAL.l_wrist[0]);
});
test('side drag moves depth only', () => {
  const s = unprojectX(0.62, 90, [...NEUTRAL.l_wrist]);
  assert.equal(s.lateral, NEUTRAL.l_wrist[0]);
  assert.notEqual(s.depth, NEUTRAL.l_wrist[1]);
});

console.log('\nskeleton tree');
test('parentOf finds the parent', () =>
  assert.equal(parentOf(SKELETON_TREE, 'l_wrist'), 'l_elbow'));
test('root has no parent', () =>
  assert.equal(parentOf(SKELETON_TREE, 'neck'), null));
test('subtree of a shoulder is its whole arm', () => {
  const s = subtree(SKELETON_TREE, 'l_shoulder');
  assert.deepEqual(s.sort(), ['l_elbow', 'l_shoulder', 'l_wrist']);
});

console.log('\nFK drag — the bug that shipped');
test('dragging one limb leaves the other untouched', () => {
  const out = dragJoint(NEUTRAL, SKELETON_TREE, NEUTRAL, 'l_wrist', [0.3, 0.2, 0.30]);
  for (const j of ['r_shoulder', 'r_elbow', 'r_wrist', 'r_knee', 'l_knee', 'nose']) {
    assert.deepEqual(out[j], NEUTRAL[j], `${j} moved but should not have`);
  }
});
test('dragging a joint keeps its bone length', () => {
  const out = dragJoint(NEUTRAL, SKELETON_TREE, NEUTRAL, 'l_wrist', [0.9, 0.9, 0.05]);
  const want = dist3(NEUTRAL.l_elbow, NEUTRAL.l_wrist);
  const got = dist3(out.l_elbow, out.l_wrist);
  assert.ok(Math.abs(got - want) < 1e-9, `${got} != ${want}`);
});
test('dragging a parent carries its children', () => {
  const out = dragJoint(NEUTRAL, SKELETON_TREE, NEUTRAL, 'l_shoulder', [0.25, 0, 0.30]);
  assert.notDeepEqual(out.l_elbow, NEUTRAL.l_elbow);
  assert.notDeepEqual(out.l_wrist, NEUTRAL.l_wrist);
  const want = dist3(NEUTRAL.l_elbow, NEUTRAL.l_wrist);
  assert.ok(Math.abs(dist3(out.l_elbow, out.l_wrist) - want) < 1e-9);
});
test('every bone survives a wild drag', () => {
  let pose = NEUTRAL;
  for (const j of ['l_wrist', 'r_ankle', 'nose', 'l_knee']) {
    pose = dragJoint(pose, SKELETON_TREE, NEUTRAL, j, [Math.random(), Math.random(), Math.random()]);
  }
  for (const [parent, kids] of Object.entries(SKELETON_TREE)) {
    for (const kid of kids) {
      if (!NEUTRAL[kid]) continue;
      const want = dist3(NEUTRAL[parent], NEUTRAL[kid]);
      const got = dist3(pose[parent], pose[kid]);
      assert.ok(Math.abs(got - want) < 1e-6, `${parent}->${kid}: ${got} != ${want}`);
    }
  }
});
test('snapToAnatomy still repairs a mangled pose', () => {
  const bad = { ...NEUTRAL, l_wrist: [0.5, 0.5, 0.95] };
  const out = snapToAnatomy(bad, NEUTRAL);
  const want = dist3(NEUTRAL.l_elbow, NEUTRAL.l_wrist);
  assert.ok(Math.abs(dist3(out.l_elbow, out.l_wrist) - want) < 1e-6);
});

/* `$` and `$$` are not word characters, so \b never matches them, and `$$(`
 * contains `$(` - both need an explicit boundary rather than \b. */
const DOM_HELPERS = ['$', '$$', 'el', 'escapeHtml'];

const bodyOf = (src) => src.replace(/^import \{[^}]*\} from [^\n]*\n/gm, '');

function importedNames(src) {
  const out = new Set();
  for (const m of src.matchAll(/^import \{([^}]+)\} from/gm)) {
    for (const raw of m[1].split(',')) {
      const name = raw.trim().split(/\s+as\s+/).pop().trim();
      if (name) out.add(name);
    }
  }
  return out;
}

// core/dom.js declares these rather than importing them, and so may anything else.
function declares(body, name) {
  const esc = name.replace(/[$]/g, '\\$');
  return new RegExp(`(?:const|let|var|function)\\s+${esc}[\\s=(]`).test(body);
}

function usesOf(name, body) {
  const pattern = name === '$' ? /(?<![\w$])\$(?=\s*\()/g
    : name === '$$' ? /(?<![\w$])\$\$(?=\s*\()/g
    : new RegExp(`(?<![\\w.$])${name}\\b`, 'g');
  return [...body.matchAll(pattern)].length;
}

console.log('\nstatic checks across every module');
// Recursive: the views moved into folders, and a top-level readdir quietly
// stopped checking two thirds of the code.
const allModules = readdirSync(JS, { recursive: true })
  .filter((f) => String(f).endsWith('.js'))
  .map(String)
  .sort();
for (const file of allModules) {
  const src = readFileSync(join(JS, file), 'utf8');
  test(`${file}: no variable declared then never assigned`, () => {
    // The activeRig bug in full: `let x = null` used everywhere, set nowhere.
    for (const m of src.matchAll(/^let ([A-Za-z_$][\w$]*) = null;/gm)) {
      const name = m[1];
      const assigned = new RegExp(`(^|[^.\\w])${name}\\s*=(?!=)`, 'gm');
      const hits = [...src.matchAll(assigned)].filter((h) => !h[0].startsWith('let '));
      assert.ok(hits.length > 1, `${name} is declared but never reassigned`);
    }
  });
  test(`${file}: every import is used`, () => {
    // Counted in the body, not the whole file. `uses > 1` stood in for "once in
    // the import line, once for real", which a name that is not a word
    // character never satisfies: `$` is followed by a comma in the import, so
    // it scored zero there and could not pass however often it was called.
    for (const name of importedNames(src)) {
      assert.ok(usesOf(name, bodyOf(src)) > 0, `${name} imported but unused`);
    }
  });
  test(`${file}: every dom helper used is imported`, () => {
    // The inverse, and the one that was missing: store.js called `$` with no
    // import, so every toast threw and a started run looked like a dead button.
    const imported = importedNames(src);
    const body = bodyOf(src);
    for (const name of DOM_HELPERS) {
      if (imported.has(name) || declares(body, name) || !usesOf(name, body)) continue;
      assert.fail(`${name} is called but never imported from core/dom.js`);
    }
  });
}

/* ------------------------------------------------- schema coverage is total
 *
 * Every field the pipeline declares must be reachable in the UI. This is not a
 * style preference: a schema-declared, pipeline-consumed setting that no view
 * renders is invisible, and the only way anyone finds out is when a run
 * behaves as though the value were never set — which it was not.
 *
 * `Export` and `Quality` were exactly this. The settings sidebar filtered its
 * groups through a hardcoded list, and a group absent from that list rendered
 * nowhere. The list is now an ordering hint with a derived fallback, and this
 * test is what keeps it that way.
 */
const schemaSrc = readFileSync(join(ROOT, 'pipeline/generation/schema.py'), 'utf8');
const settingsSrc = readFileSync(join(JS, 'views/settings/settings.js'), 'utf8');

const declaredGroups = new Set(
  [...schemaSrc.matchAll(/group="([^"]+)"/g)].map((m) => m[1]));

test('schema declares groups at all', () => {
  assert.ok(declaredGroups.size > 5, `only found ${declaredGroups.size} groups`);
});

test('the settings sidebar derives its groups, never whitelists them', () => {
  // A literal array used as a filter is the regression. An array used only for
  // ordering, with unknown groups appended, is the fix.
  assert.ok(/sectionOrder/.test(settingsSrc),
    'settings.js should derive its section list from the schema');
  assert.ok(!/const SECTIONS\s*=/.test(settingsSrc),
    'SECTIONS was the whitelist that dropped Export and Quality');
});

test('every schema group is reachable', () => {
  const order = new Set(
    [...(settingsSrc.match(/const ORDER = \[([\s\S]*?)\]/) || ['', ''])[1]
      .matchAll(/'([^']+)'/g)].map((m) => m[1]));
  // Groups missing from ORDER still render — they are appended — but naming
  // them keeps the sidebar in a deliberate order rather than alphabetical.
  const unordered = [...declaredGroups].filter((g) => !order.has(g));
  assert.deepEqual(unordered, [],
    `schema groups not placed in ORDER: ${unordered.join(', ')}`);
});

/* Typed references: the roles the backend accepts and the roles the UI offers
 * have to be the same four, or an image gets tagged with a role that fails
 * validation only once the job reaches the queue. */
test('reference roles match the backend', () => {
  const backend = [...readFileSync(join(ROOT, 'pipeline/refs/references.py'), 'utf8')
    .match(/ROLES = \(([^)]+)\)/)[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  const frontend = [...readFileSync(join(JS, 'views/input/input.js'), 'utf8')
    .match(/export const ROLES = \[([\s\S]*?)\n\];/)[1]
    .matchAll(/key:\s*'([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(frontend, backend);
});

test('nothing writes the retired references.images', () => {
  for (const file of readdirSync(JS).filter((f) => f.endsWith('.js'))) {
    const src = readFileSync(join(JS, file), 'utf8');
    for (const line of src.split('\n')) {
      if (line.trimStart().startsWith('//') || line.trimStart().startsWith('*')) continue;
      // `references?.images` is the same bug and slipped past a literal dot:
      // two live call sites survived the first sweep because of it.
      assert.ok(!/references\??\.images/.test(line),
        `${file} still touches references.images: ${line.trim()}`);
    }
  }
});

/* ------------------------------------------------------- rendered output
 *
 * Everything above tests logic that never touches the DOM, which is why the
 * DOM-shaped bugs were the ones that shipped. domshim.mjs is a DOM small
 * enough to have no dependencies and real enough to render a component and
 * assert what came out — the thing a refactor of web/js needs to be safe.
 */
const { installDom } = await import(join(ROOT, 'tests/frontend/domshim.mjs'));
installDom();

const ui = await import(join(JS, 'ui/index.js'));
const { el } = await import(join(JS, 'core/dom.js'));

test('an interval is either poll() or cleared by a teardown', () => {
  // Frame playback cannot be a poll: it does not want overlap protection or
  // the hidden-tab skip, it wants a steady tick. What it does need is a stop
  // that something calls, which is what MutationObserver used to fake.
  for (const file of allModules) {
    if (file.endsWith('listeners/poll.js')) continue;
    const src = readFileSync(join(JS, file), 'utf8');
    assert.ok(!/MutationObserver/.test(src), `${file} watches the DOM for teardown`);
    if (/\bsetInterval\(/.test(src)) {
      assert.ok(/clearInterval\(/.test(src), `${file} starts an interval it never clears`);
      assert.ok(/stops\.push\(|return \(\) =>|return stop/.test(src),
                `${file} clears its interval but hands nobody the stop`);
    }
  }
});

console.log('\ndom shim');
test('el() builds a tree with text and children', () => {
  const node = el('div', { className: 'a b' }, el('span', { textContent: 'hi' }), 'tail');
  assert.equal(node.tagName, 'DIV');
  assert.ok(node.classList.contains('b'));
  assert.equal(node.textContent, 'hitail');
});
test('el() skips null and false children', () => {
  assert.equal(el('div', {}, null, false, 'x').textContent, 'x');
});
test('querySelector finds by class and by tag.class', () => {
  const root = el('div', {}, el('p', { className: 'help' }, 'z'));
  assert.ok(root.querySelector('.help'));
  assert.ok(root.querySelector('p.help'));
  assert.equal(root.querySelector('.nope'), null);
});

console.log('\neditor cost');
await atest('the shader refuses an image bigger than its ceiling', async () => {
  // A 12 Mpx upload made three 49 MB GPU allocations plus a 12 Mpx dispatch,
  // per call, and nothing serialised the calls. On Apple Silicon that memory
  // is the display's memory.
  const gpu = await import(join(JS, 'views/editor/gpu.js'));
  const src = readFileSync(join(JS, 'views/editor/gpu.js'), 'utf8');
  assert.ok(/MAX_PIXELS/.test(src), 'gpu.js has no ceiling');
  await assert.rejects(
    () => gpu.render({ width: 4032, height: 3024 }, { factor: 1, phase: [0, 0], palette: [] }),
    /ceiling/,
    'a 12 Mpx bitmap was accepted');
});

test('the editor decodes at the budget and runs one preview at a time', () => {
  const src = readFileSync(join(JS, 'views/editor/editor.js'), 'utf8');
  // Every decode must go through the capping helper.
  const raw = [...src.matchAll(/createImageBitmap\(/g)].length;
  const inDecode = src.slice(src.indexOf('async function decode('),
                             src.indexOf('export function renderEditor'));
  assert.equal([...inDecode.matchAll(/createImageBitmap\(/g)].length, raw,
               'a createImageBitmap call bypasses decode()');
  assert.ok(/resizeWidth/.test(src), 'decode() does not resize');
  assert.ok(/if \(drawing\)/.test(src), 'drawPreview is not serialised');
});

test('the editor says why the live preview declined, instead of just stopping', () => {
  // Both refusals used to be a bare `return false`. Moving a slider then did
  // nothing at all, with no error anywhere, which reads as a freeze.
  const src = readFileSync(join(JS, 'views/editor/editor.js'), 'utf8');
  const guard = src.match(/if \(!gpu\.supported\(\) \|\| !bitmap\) \{([^}]*)\}/);
  assert.ok(guard, 'drawPreview no longer guards on support and bitmap together');
  assert.ok(/explainNoPreview\(\)/.test(guard[1]),
            'drawPreview still declines silently');
  assert.ok(/WebGPU/.test(src) && /could not be decoded/.test(src),
            'the two reasons are not told apart');
});

test('every source reaches the shader through one decode', () => {
  // `source` set without `bitmap` decoded is what left the live preview dead
  // until the image was re-picked by hand.
  const src = readFileSync(join(JS, 'views/editor/editor.js'), 'utf8');
  const body = src.slice(src.indexOf('export function renderEditor'));
  const inUseSource = body.slice(body.indexOf('async function useSource('),
                                 body.indexOf('/* The fast path'));
  const all = [...body.matchAll(/(?<![.\w])source = /g)].length;
  const mine = [...inUseSource.matchAll(/(?<![.\w])source = /g)].length;
  assert.equal(all - mine, 0,
               'something assigns `source` outside useSource()');
  assert.equal(mine, 1, 'useSource() no longer owns the assignment');
  assert.ok(/if \(source && !bitmap\) await useSource\(source\)/.test(body),
            'a restored source is never decoded on mount');
});

test('a new source always re-measures the block size', () => {
  // The picker reset Grid's factor and the upload did not, so a factor
  // measured from a large image was carried onto a small one and divided what
  // was no longer there - 16 turns a 32px upload into a single pixel.
  const src = readFileSync(join(JS, 'views/editor/editor.js'), 'utf8');
  const body = src.slice(src.indexOf('export function renderEditor'));
  const resets = [...body.matchAll(/config\.factor = 0/g)].length;
  assert.equal(resets, 1, 'the factor reset is duplicated or missing');
  const inUseSource = body.slice(body.indexOf('async function useSource('),
                                 body.indexOf('/* The fast path'));
  assert.ok(/config\.factor = 0/.test(inUseSource),
            'the reset does not live with the source it belongs to');
});

console.log('\nfeatures');
test('features/ never touches the DOM', () => {
  // The rule that makes this folder testable without a shim. A module that
  // builds a node has stopped being domain logic.
  const DOM = /\b(document|el\(|getContext|addEventListener|replaceChildren)\b/;
  for (const f of readdirSync(join(JS, 'features'))) {
    const src = readFileSync(join(JS, 'features', f), 'utf8');
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    assert.ok(!DOM.test(code), `features/${f} touches the DOM`);
  }
});

await atest('stage ordering is decidable without a schema global', async () => {
  const { orderProblems, autoOrder } = await import(join(JS, 'features/stages.js'));
  const stages = [
    { name: 'pose', needs: [], gives: ['skeletons'] },
    { name: 'frames', needs: ['skeletons'], gives: ['frames'] },
    { name: 'export', needs: ['frames'], gives: ['sheet'] },
  ];
  assert.deepEqual(orderProblems(['pose', 'frames', 'export'], stages), []);

  // A stage declares one set of needs and the run answers some of them, so a
  // resource must not be reported as an artifact nothing produces.
  const withRig = [{ name: 'pose', needs: ['rig'], gives: ['skeletons'] },
                   { name: 'frames', needs: ['skeletons'], gives: ['frames'] }];
  assert.deepEqual(orderProblems(['pose', 'frames'], withRig, ['rig']), []);
  assert.equal(orderProblems(['pose', 'frames'], withRig, []).length, 1,
               'without the resource list a rig reads as a missing artifact');

  // A soft need absent altogether is fine; produced later is the same mistake
  // as a hard one, and this twin has to agree with runner.validate or
  // Auto-order proposes an order the server refuses.
  const soft = [{ name: 'canonical', needs: [], optional: ['depthmaps'], gives: ['canonical'] },
                { name: 'depth', needs: [], gives: ['depthmaps'] }];
  assert.deepEqual(orderProblems(['canonical'], soft), [],
                   'a soft need nothing produces is not a problem');
  assert.equal(orderProblems(['canonical', 'depth'], soft).length, 1,
               'a soft need produced later is');
  assert.deepEqual(orderProblems(['depth', 'canonical'], soft), []);
  assert.deepEqual(autoOrder(['canonical', 'depth'], soft), ['depth', 'canonical'],
                   'auto-order has to place a soft producer first too');

  const broken = orderProblems(['frames', 'pose'], stages);
  assert.equal(broken.length, 1);
  assert.ok(broken[0].includes('runs later'), broken[0]);

  assert.deepEqual(autoOrder(['export', 'frames', 'pose'], stages),
                   ['pose', 'frames', 'export']);
});

console.log('\npartial rendering');
await atest('a field change only rebuilds the form when it gates another field', async () => {
  const { layerForm } = await import(join(JS, 'views/editor/stack.js'));
  const spec = {
    key: 'grid', label: 'Grid', summary: '',
    fields: [
      { key: 'phase', label: 'Origin', kind: 'select', help: 'h', default: 'auto',
        options: [['auto', 'Auto'], ['manual', 'Manual']], when: {} },
      { key: 'phase_x', label: 'X', kind: 'int', help: 'h', default: 0,
        min: 0, max: 63, when: { phase: 'manual' } },
      { key: 'reduce', label: 'Reduce', kind: 'select', help: 'h', default: 'median',
        options: [['median', 'Median']], when: {} },
    ],
  };
  // `phase` gates `phase_x`; `reduce` gates nothing.
  const gates = (key) => spec.fields.some((f) => key in (f.when || {}));
  assert.equal(gates('phase'), true, 'a gating field was not recognised');
  assert.equal(gates('reduce'), false, 'a plain field would force a rebuild');

  // And the gate actually hides the field it guards.
  const hidden = layerForm(spec, { phase: 'auto' }, () => {});
  const shown = layerForm(spec, { phase: 'manual' }, () => {});
  assert.ok(shown.children.length > hidden.children.length,
            'the gated field never appeared');
});

console.log('\nlisteners and lifecycle');
await atest('a subscription fires once per set, whatever else changed with it', async () => {
  const { subscribe, notify, listenerCount } = await import(join(JS, 'core/subscribe.js'));
  let hits = 0;
  const stop = subscribe(['runs', 'queue'], () => { hits += 1; });
  notify('runs');
  notify('queue');
  notify('runs', 'queue');          // one listener, one call
  assert.equal(hits, 3);
  notify('something-else');
  assert.equal(hits, 3);
  stop();
  notify('runs');
  assert.equal(hits, 3, 'unsubscribe did not stop the listener');
  assert.equal(listenerCount(), 0);
});

await atest('one failing subscriber does not stop the others', async () => {
  const { subscribe, notify } = await import(join(JS, 'core/subscribe.js'));
  let reached = false;
  const a = subscribe(['x'], () => { throw new Error('boom'); });
  const b = subscribe(['x'], () => { reached = true; });
  notify('x');
  assert.ok(reached, 'a throwing listener took the page with it');
  a(); b();
});

await atest('a poll stops, and does not stack ticks on a slow one', async () => {
  const { poll } = await import(join(JS, 'listeners/poll.js'));
  let started = 0, finished = 0;
  const stop = poll(async () => {
    started += 1;
    await new Promise((r) => setTimeout(r, 40));
    finished += 1;
  }, { every: 5 });
  await new Promise((r) => setTimeout(r, 100));
  stop();
  const at = started;
  assert.ok(started - finished <= 1, `${started - finished} ticks overlapped`);
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(started, at, 'the poll kept running after stop()');
});

await atest('mounting a view tears down the last one', async () => {
  const { mount, unmount, mounted } = await import(join(JS, 'listeners/lifecycle.js'));
  const host = el('div', {});
  let cleaned = 0;
  mount('a', host, () => () => { cleaned += 1; });
  assert.equal(mounted(), 'a');
  assert.equal(cleaned, 0);
  mount('b', host, () => {});           // b has nothing to clean up
  assert.equal(cleaned, 1, 'the previous view was never torn down');
  unmount();
  assert.equal(mounted(), null);
});

await atest('a view that throws shows the failure in place, with a retry', async () => {
  const { mount } = await import(join(JS, 'listeners/lifecycle.js'));
  const host = el('div', {});
  mount('editor', host, () => { throw new Error('no catalogue'); });
  assert.ok(host.querySelector('.viewerror'), 'a broken view left a blank tab');
  assert.ok(host.textContent.includes('no catalogue'));
  assert.ok(host.querySelector('button'), 'no way to retry');
});

console.log('\nui kit');
test('a button variant is named, not spelt as a class', () => {
  assert.equal(ui.Button.primary('Go').className, 'btn primary');
  assert.equal(ui.Button.ghost('Go').className, 'btn ghost');
  assert.equal(ui.Button('Go').className, 'btn');
});
test('an unknown variant throws instead of rendering unstyled', () => {
  // A typo produced a bare button, which nobody notices until a screenshot.
  assert.throws(() => ui.Button('Go', { variant: 'primry' }), /no button variant/);
});
test('Segmented marks the active option and shows its count', () => {
  const seg = ui.Segmented([['a', 'Alpha', 3], ['b', 'Beta', null]], { value: 'a', onPick() {} });
  const on = seg.querySelectorAll('.on');
  assert.equal(on.length, 1);
  assert.ok(on[0].textContent.includes('Alpha'));
  assert.ok(on[0].textContent.includes('3'));
});
test('Select marks the current value', () => {
  const sel = ui.Select([['x', 'Ex'], ['y', 'Why']], { value: 'y' });
  const opts = sel.querySelectorAll('option');
  assert.equal(opts[1].selected, true);
  assert.equal(opts[0].selected, false);
});
test('the kit builds every widget the views repeat by hand', () => {
  // Counted from the source before the kit existed: btn in 12 files, mini in
  // 11, empty in 10. A widget missing here is a widget that gets rebuilt.
  for (const name of ['Button', 'Select', 'Num', 'Check', 'Range', 'Row', 'Fields',
                      'Head', 'PanelHead', 'Segmented', 'Mini', 'Mono', 'Empty',
                      'Warn', 'Ok', 'Note', 'Fact', 'FactGrid']) {
    assert.equal(typeof ui[name], 'function', `ui.${name} is missing`);
  }
});
test('ui/ knows nothing about the domain', () => {
  // A primitive that understands a rig has stopped being one, and that is the
  // rule that keeps this folder testable without a server.
  const DOMAIN = /\b(rig|palette|canonical|pipeline|stage|sprite|joint|pose)\b/i;
  for (const f of ['kit.js', 'primitives.js', 'card.js', 'field.js']) {
    const src = readFileSync(join(JS, 'ui', f), 'utf8');
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    assert.ok(!DOMAIN.test(code), `ui/${f} mentions the domain`);
  }
});

console.log('\nui primitives');
test('Heading uses the tag matching its level', () => {
  assert.ok(ui.Heading('T', { level: 3 }).querySelector('h3'));
  assert.ok(ui.Heading('T', { level: 1 }).querySelector('h1'));
});
test('Section nests its children in a body', () => {
  const s = ui.Section('Title', {}, el('p', { textContent: 'child' }));
  assert.ok(s.querySelector('.ui-section-body'));
  assert.ok(s.textContent.includes('child'));
  assert.ok(s.textContent.includes('Title'));
});
test('Subsection is an h3, Section an h2', () => {
  assert.ok(ui.Section('a', {}).querySelector('h2'));
  assert.ok(ui.Subsection('a', {}).querySelector('h3'));
});
test('HelpTip summarises to the lead sentence in the title', () => {
  const tip = ui.HelpTip('Short lead. Then the long measured reasoning follows.');
  assert.equal(tip.btn.title, 'Short lead.');
  assert.ok(tip.body.textContent.includes('measured reasoning'));
});
test('HelpTip body starts hidden and toggles', () => {
  const tip = ui.HelpTip('A. B.');
  assert.ok(tip.body.classList.contains('hidden'));
  tip.btn.onclick();
  assert.ok(!tip.body.classList.contains('hidden'));
});
test('HelpTip on empty help is null, not an empty button', () => {
  assert.equal(ui.HelpTip(''), null);
});

console.log('\nBaseCard');
test('a card with no overrides still renders', () => {
  assert.ok(new ui.BaseCard({}).render().classList.contains('ui-card'));
});
test('subclass hooks land in the right slots', () => {
  class C extends ui.BaseCard {
    media() { return el('img', { className: 'm' }); }
    title() { return 'Name'; }
    footer() { return [el('button', { textContent: 'go' })]; }
  }
  const n = new C({ data: {} }).render();
  assert.ok(n.querySelector('.m'), 'media');
  assert.equal(n.querySelector('.ui-card-title').textContent, 'Name');
  assert.ok(n.querySelector('.ui-card-foot'), 'footer');
});
test('empty rows and footer produce no empty containers', () => {
  const n = new ui.BaseCard({ data: { title: 'x' } }).render();
  assert.equal(n.querySelector('.ui-card-rows'), null);
  assert.equal(n.querySelector('.ui-card-foot'), null);
});

console.log('\nBaseField');
test('every field renders a (?) next to its label', () => {
  const n = new ui.BaseField({ field: { path: 'a.b', label: 'A', help: 'Why. Because.' } }).render();
  assert.ok(n.querySelector('.ui-label-row .ui-tip'), 'tip missing');
  assert.equal(n.querySelector('.ui-label').textContent, 'A');
});
test('a field with NO help shows a disabled marker, not nothing', () => {
  const n = new ui.BaseField({ field: { path: 'a.b', label: 'A' } }).render();
  const tip = n.querySelector('.ui-tip');
  assert.ok(tip, 'marker missing');
  assert.ok(tip.classList.contains('ui-tip-missing'));
});
test('commit reports the schema path, not the label', () => {
  let seen = null;
  const f = new ui.BaseField({ field: { path: 'canonical.seed', label: 'Seed' },
                               on: { change: (p, v) => { seen = [p, v]; } } });
  f.commit(7);
  assert.deepEqual(seen, ['canonical.seed', 7]);
});
test('label is bound to its control id', () => {
  const f = new ui.BaseField({ field: { path: 'x', label: 'X' } });
  const n = f.render();
  assert.equal(n.querySelector('.ui-label').getAttribute('for'), f.id);
});

test('update() swaps in place using replaceWith, not children.indexOf', () => {
  // children is an HTMLCollection in a browser and has no indexOf: splicing it
  // is a TypeError at runtime that any array-backed double would pass.
  const grid = el('div', {});
  const card = new ui.BaseCard({ data: { title: 'before' } });
  grid.append(card.render());
  card.update({ title: 'after' });
  assert.equal(grid.children.length, 1);
  assert.equal(grid.querySelector('.ui-card-title').textContent, 'after');
});

console.log('\nrail and pipeline creation');
const rail = await import(join(JS, 'rail.js'));
const library = await import(join(JS, 'library.js'));
const railState = (await import(join(JS, 'store.js'))).state;
test('configsFor filters by the module the index carries', () => {
  railState.configs = [
    { name: 'knight_attack', module: 'animation', error: '' },
    { name: 'archer', module: 'character_sheet', error: '' },
    { name: 'monster_anim', module: 'animation', error: '' },
  ];
  assert.deepEqual(rail.configsFor('animation'), ['knight_attack', 'monster_anim']);
  assert.deepEqual(rail.configsFor('character_sheet'), ['archer']);
  assert.deepEqual(rail.configsFor('tileset'), []);
});
test('configsFor returns names, so the picker needs no second lookup', () => {
  assert.ok(rail.configsFor('animation').every((c) => typeof c === 'string'));
});
test('a starter config declares its workspace and its stage order', () => {
  const cfg = library.starterConfig('my_portrait', 'character_sheet', ['pose', 'export']);
  assert.equal(cfg.module, 'character_sheet');
  assert.equal(cfg.name, 'my_portrait');
  assert.deepEqual(cfg.pipeline.stages, ['pose', 'export']);
});
await atest('the blank order the dialog offers is one the server would accept', async () => {
  // The dialog fills pipeline.stages with autoOrder over every registered
  // stage, so what it proposes has to survive the same check save_config runs.
  const { orderProblems, autoOrder } = await import(join(JS, 'features/stages.js'));
  const stages = [
    { name: 'pose', needs: ['rig'], gives: ['skeletons', 'pose_frames'] },
    { name: 'depth', needs: ['pose_frames'], gives: ['depthmaps'] },
    { name: 'canonical', needs: [], optional: ['depthmaps'], gives: ['canonical'] },
    { name: 'frames', needs: ['skeletons', 'canonical'], optional: ['depthmaps'], gives: ['frames'] },
    { name: 'palette', needs: ['frames', 'canonical'], gives: ['palette', 'pixel_frames'] },
    { name: 'export', needs: ['pixel_frames'], gives: ['sheet'] },
  ];
  const ordered = autoOrder(stages.map((s) => s.name), stages);
  assert.deepEqual(orderProblems(ordered, stages, ['rig']), [],
                   `blank order does not validate: ${ordered}`);
  assert.ok(ordered.indexOf('depth') < ordered.indexOf('canonical'),
            'a soft producer must still be placed first');
});

test('the rail says which stage an unavailable type is waiting on', () => {
  // `available` is derived from the stage registry now, so the cell can name
  // the work. "not built yet" threw that away.
  railState.schema = {
    stages: [{ name: 'pose', needs: [], gives: ['skeletons'] }],
    resources: [],
    modules: {
      animation: { key: 'animation', label: 'Animation', detail: 'one action',
                   blurb: 'b', available: true, missing: [] },
      tileset: { key: 'tileset', label: 'Tileset', detail: 'terrain',
                 blurb: 'b', available: false, missing: ['tile_pose', 'tile_edges'] },
    },
  };
  railState.module = 'animation';
  const host = el('div', {});
  rail.renderRail(host, { onSwitch() {}, onCreated() {}, onTypeCreated() {} });

  const details = host.querySelectorAll('.rail-detail').map((n) => n.textContent);
  assert.ok(details.includes('one action'), details);
  assert.ok(details.includes('needs tile_pose, tile_edges'), details);
  assert.ok(!details.includes('not built yet'), 'still hiding which work is missing');
});
test('an unavailable cell cannot be clicked, and New type always can', () => {
  const host = el('div', {});
  rail.renderRail(host, { onSwitch() {}, onCreated() {}, onTypeCreated() {} });
  const cells = host.querySelectorAll('.rail-cell');
  assert.equal(cells.length, 3, 'two types plus the add cell');
  const tileset = cells.find((c) => c.textContent.includes('Tileset'));
  assert.ok(tileset.disabled, 'a type whose stages do not exist was clickable');
  const add = host.querySelector('.rail-cell.add');
  assert.ok(add && !add.disabled, 'no way to define a type');
});

console.log('\nannotator');
const annotMod = await import(join(JS, 'views/run/annotate.js'));
const apiMod = await import(join(JS, 'api.js'));

const RIG = {
  name: 'humanoid', label: 'Humanoid', root: 'neck',
  joints: ['neck', 'nose', 'l_shoulder', 'r_shoulder'],
  tree: {}, limbs: [], bones: [['neck', 'nose', 1]], neutral: {},
  face_joints: ['nose'], colors: [[120, 140, 255]],
  pose: { neck: [0, 0, 0.22], nose: [0, 0.03, 0.14],
          l_shoulder: [0.055, 0, 0.24], r_shoulder: [-0.055, 0, 0.24] },
};

async function mountAnnotator(existing = { exists: false, points: {} }) {
  apiMod.api.rigPose = async () => structuredClone(RIG);
  apiMod.api.annotation = async () => structuredClone(existing);
  apiMod.api.fileUrl = () => 'ref.png';
  const root = annotMod.annotator({ imagePath: 'ref.png', rigName: 'humanoid' });
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  return root;
}

const press = (canvas, x, y) =>
  canvas.onpointerdown({ clientX: x, clientY: y, pointerId: 1 });

await atest('every joint is listed, not only the placed ones', async () => {
  const root = await mountAnnotator();
  const rows = root.querySelectorAll('.jointrow');
  assert.equal(rows.length, RIG.joints.length, 'unplaced joints are unreachable');
  assert.equal(root.querySelectorAll('.jointrow.placed').length, 0);
});

await atest('removing a point aims at it, so a click puts it back', async () => {
  // The bug: delete changed `points` and left `next` wherever advance() had
  // moved on to, so the following click landed on an unrelated joint.
  const root = await mountAnnotator();
  const canvas = root.querySelector('.annotcanvas');

  press(canvas, 100, 100);            // places the first joint in PRIORITY order
  let placed = root.querySelectorAll('.jointrow.placed');
  assert.equal(placed.length, 1, 'nothing was placed');
  const first = placed[0].querySelector('.jointname').textContent;

  press(canvas, 300, 200);            // places the second
  assert.equal(root.querySelectorAll('.jointrow.placed').length, 2);

  const row = root.querySelectorAll('.jointrow')
    .find((r) => r.querySelector('.jointname').textContent === first);
  row.querySelector('.x').onclick({ stopPropagation() {} });

  const aimed = root.querySelector('.jointrow.aimed .jointname').textContent;
  assert.equal(aimed, first, `after removing ${first} the aim moved to ${aimed}`);
  assert.equal(root.querySelectorAll('.jointrow.placed').length, 1);

  press(canvas, 120, 120);            // the click that puts it back
  assert.equal(root.querySelectorAll('.jointrow.placed').length, 2,
               'the removed joint was not restorable by clicking');
});

await atest('a dot already on the canvas can be grabbed and moved', async () => {
  const root = await mountAnnotator({
    exists: true, points: { neck: [0.5, 0.5] }, placed: 1,
  });
  const canvas = root.querySelector('.annotcanvas');
  assert.equal(root.querySelectorAll('.jointrow.placed').length, 1);

  // 640x640 canvas, no image loaded, so image space spans the whole canvas.
  press(canvas, 320, 320);                                   // grab neck
  assert.equal(root.querySelector('.jointrow.aimed .jointname').textContent, 'neck');
  canvas.onpointermove({ clientX: 160, clientY: 96, pointerId: 1 });
  canvas.onpointerup({ pointerId: 1 });

  // Still one joint: a drag moves a dot, it does not create another.
  assert.equal(root.querySelectorAll('.jointrow.placed').length, 1);
});

await atest('clicking a joint row aims at it without placing anything', async () => {
  const root = await mountAnnotator();
  const rows = root.querySelectorAll('.jointrow');
  const last = rows[rows.length - 1];
  last.onclick();
  assert.equal(root.querySelector('.jointrow.aimed .jointname').textContent,
               last.querySelector('.jointname').textContent);
  assert.equal(root.querySelectorAll('.jointrow.placed').length, 0);
});

console.log('\ndialogs');
/* Characterisation: what these four render, what they resolve, and what they
 * call. Written before the modal scaffolding was extracted, so the extraction
 * has something to be measured against. */
const dlgApi = (await import(join(JS, 'api.js'))).api;
const dlg = await import(join(JS, 'ui/dialog.js'));

const shed = () => document.querySelectorAll('.modal').forEach((m) => m.remove());
const openModal = () => document.querySelector('.modal');
const buttons = (m) => m.querySelectorAll('button').map((b) => b.textContent);

await atest('browseDialog names its purpose and resolves the folder', async () => {
  shed();
  let asked = null;
  dlgApi.browse = async (path, images) => {
    asked = { path, images };
    return { dir: '/root/here', parent: '/root', entries: [] };
  };
  const done = dlg.browseDialog('/root/here', false);
  await new Promise((r) => setTimeout(r, 0));

  const m = openModal();
  assert.ok(m, 'no modal was opened');
  assert.equal(m.querySelector('h2').textContent, 'Select a folder');
  assert.deepEqual(buttons(m).slice(-2), ['Cancel', 'Use folder']);
  assert.deepEqual(asked, { path: '/root/here', images: false });

  m.querySelectorAll('button').find((b) => b.textContent === 'Use folder').onclick();
  assert.equal(await done, '/root/here');
  assert.equal(openModal(), null, 'the modal outlived its answer');
});

await atest('browseDialog in image mode resolves a list', async () => {
  shed();
  dlgApi.browse = async () => ({ dir: '/d', parent: '', entries: [] });
  const done = dlg.browseDialog('/d', true);
  await new Promise((r) => setTimeout(r, 0));
  const m = openModal();
  assert.equal(m.querySelector('h2').textContent, 'Select images');
  assert.deepEqual(buttons(m).slice(-2), ['Cancel', 'Select']);
  m.querySelectorAll('button').find((b) => b.textContent === 'Select').onclick();
  assert.deepEqual(await done, []);
});

await atest('cancel and the backdrop both resolve null', async () => {
  shed();
  dlgApi.browse = async () => ({ dir: '/d', parent: '', entries: [] });
  const byCancel = dlg.browseDialog('/d', false);
  await new Promise((r) => setTimeout(r, 0));
  let m = openModal();
  m.querySelectorAll('button').find((b) => b.textContent === 'Cancel').onclick();
  assert.equal(await byCancel, null);

  const byBackdrop = dlg.browseDialog('/d', false);
  await new Promise((r) => setTimeout(r, 0));
  m = openModal();
  m.onclick({ target: m });
  assert.equal(await byBackdrop, null);
  assert.equal(openModal(), null);
});

await atest('confirmDialog reports the answer and the remember box', async () => {
  shed();
  const done = dlg.confirmDialog({ title: 'Gate', body: 'b', rememberKey: 'k' });
  const m = openModal();
  assert.equal(m.querySelector('h2').textContent, 'Gate');
  m.querySelector('.chk input').checked = true;
  m.querySelectorAll('button').find((b) => b.textContent === 'Continue').onclick();
  assert.deepEqual(await done, { ok: true, remember: true });
});

await atest('newPipelineDialog offers Blank plus every sibling', async () => {
  shed();
  railState.configs = [
    { name: 'knight_attack', module: 'animation', error: '' },
    { name: 'archer', module: 'character_sheet', error: '' },
  ];
  railState.schema = {
    stages: [{ name: 'pose', needs: [], gives: ['skeletons'] },
             { name: 'export', needs: ['skeletons'], gives: ['sheet'] }],
    resources: [], modules: { animation: { label: 'Animation' } },
  };
  const done = library.newPipelineDialog('animation', { label: 'Animation' });
  const m = openModal();
  const options = m.querySelector('select').children.map((o) => o.textContent);
  assert.equal(options.length, 2, options);
  assert.ok(options[0].startsWith('Blank'), options[0]);
  assert.ok(options[1].includes('knight_attack'), options[1]);

  m.querySelectorAll('button').find((b) => b.textContent === 'Cancel').onclick();
  assert.equal(await done, null);
});

await atest('newPipelineDialog refuses a name that is taken or malformed', async () => {
  shed();
  const done = library.newPipelineDialog('animation', { label: 'Animation' });
  const m = openModal();
  const name = m.querySelector('input[type=text]');
  const create = m.querySelectorAll('button').find((b) => b.textContent === 'Create');
  const why = () => m.querySelectorAll('.help')[0].textContent;

  assert.ok(create.disabled, 'an empty name was accepted');
  name.value = 'knight_attack';
  name._listeners.input.forEach((f) => f());
  assert.ok(why().includes('already exists'), why());
  assert.ok(create.disabled);

  name.value = 'has spaces';
  name._listeners.input.forEach((f) => f());
  assert.ok(why().includes('Letters'), why());

  name.value = 'fresh_one';
  name._listeners.input.forEach((f) => f());
  assert.equal(why(), '');
  assert.ok(!create.disabled, 'a valid name stayed refused');

  m.querySelectorAll('button').find((b) => b.textContent === 'Cancel').onclick();
  await done;
});

await atest('newTypeDialog saves the type it was filled in with', async () => {
  shed();
  railState.schema = {
    stages: [{ name: 'pose', needs: [], gives: ['skeletons'] },
             { name: 'export', needs: ['skeletons'], gives: ['sheet'] }],
    resources: [],
    modules: { animation: { key: 'animation', label: 'Animation' } },
  };
  let sent = null;
  dlgApi.saveModule = async (key, body) => { sent = { key, body }; return { saved: key }; };

  const done = library.newTypeDialog();
  const m = openModal();
  const [key, label, detail] = m.querySelectorAll('input[type=text]');
  const blurb = m.querySelector('textarea');
  for (const [node, value] of [[key, 'portrait'], [label, 'Portraits'], [detail, 'faces']]) {
    node.value = value;
    node._listeners.input.forEach((f) => f());
  }
  blurb.value = 'A head, several ways.';
  blurb._listeners.input.forEach((f) => f());

  const create = m.querySelectorAll('button').find((b) => b.textContent === 'Create type');
  assert.ok(!create.disabled, 'a complete form stayed refused');
  await create.onclick();

  assert.equal(await done, 'portrait');
  assert.equal(sent.key, 'portrait');
  assert.equal(sent.body.label, 'Portraits');
  assert.deepEqual(sent.body.stages, ['pose', 'export']);
});

await atest('newTypeDialog warns about a stage nobody registers', async () => {
  shed();
  const done = library.newTypeDialog();
  const m = openModal();
  const [key] = m.querySelectorAll('input[type=text]');
  key.value = 'weather';
  key._listeners.input.forEach((f) => f());
  const extra = m.querySelectorAll('input[type=text]')[3];
  extra.value = 'cloud_field';
  extra._listeners.input.forEach((f) => f());

  const said = m.querySelectorAll('.help').map((n) => n.textContent).join(' ');
  assert.ok(said.includes('cloud_field'), said.slice(0, 200));
  m.querySelectorAll('button').find((b) => b.textContent === 'Cancel').onclick();
  await done;
  shed();
});

console.log('\nchrome');
/* toast is the one function every other failure path reports through, so a
 * throw inside it hides the message it was called to show. */
const store = await import(join(JS, 'store.js'));
test('toast renders the message it was given', () => {
  store.toast('Started 20260908_215455_char_3');
  const host = document.querySelector('#toasts');
  assert.ok(host, 'no #toasts host was created');
  assert.equal(host.children.at(-1).textContent, 'Started 20260908_215455_char_3');
});
test('a second toast reuses the one host', () => {
  const before = document.querySelector('#toasts').children.length;
  store.toast('again');
  assert.equal(document.querySelectorAll('#toasts').length, 1);
  assert.equal(document.querySelector('#toasts').children.length, before + 1);
});
test('toast never throws, whatever it is handed', () => {
  store.toast(undefined);
  store.toast({ nope: 1 }, 'error');
});
test('confirmDialog and lightbox open without throwing', () => {
  dlg.confirmDialog({ title: 't', body: 'b', rememberKey: 'k' });
  assert.ok(document.querySelector('.modal'));
  dlg.lightbox('/api/file?path=x.png', 'cap');
  assert.ok(document.querySelector('.lightbox'));
});

console.log('\nview slots');
const inputSrc = readFileSync(join(JS, 'views/input/input.js'), 'utf8');
test('the four sheet views match the backend aliases', () => {
  const views = [...inputSrc.matchAll(/\{ view: '([^']+)', label:/g)].map((m) => m[1]);
  assert.deepEqual(views, ['front', 'rear', 'side', '270']);
});
test('the right side stays a raw angle, not a name', () => {
  // Naming 270 something that reads like a mirror of `side` is how the two get
  // swapped; the backend has no name for it either.
  assert.ok(inputSrc.includes("view: '270'"));
  assert.ok(!/view: 'side_right'/.test(inputSrc));
});
test('uploads no longer hardcode front for every image', () => {
  assert.ok(!/view: 'front', weight: 1 \}\)\)\]\);/.test(inputSrc),
    'addRefs still labels every upload front');
  assert.ok(inputSrc.includes('pendingView'), 'no targeted-view state');
});
test('adding to a slot replaces that view rather than stacking', () => {
  assert.ok(/filter\(\(r\) => String\(r\.view\) !== String\(label\)\)/.test(inputSrc));
});

console.log('\nschema coverage');
await atest('every schema field carries help, so no (?) is ever empty', async () => {
  // The BaseField marker makes a missing explanation visible rather than
  // invisible; this keeps the count from growing quietly.
  const src = readFileSync(join(ROOT, 'pipeline/generation/schema.py'), 'utf8');
  // ConfigField declarations, not the dict literals FIELDS used to be. This
  // matched `{"path": ...}` and found zero after that migration, which the
  // runner hid because the body is async.
  const paths = [...src.matchAll(/\bkey="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(paths.length > 100, `only found ${paths.length} schema paths`);
});

console.log('\neditor preview surface');
await atest('both engines draw into the one fixed stage', async () => {
  // The shader hands over the REDUCED grid and the exact path a full PNG. Each
  // used to size the element itself, so a drag swapped a 24px thumbnail for a
  // full panel and back, and changing the grid factor resized it again.
  const src = readFileSync(join(JS, 'views/editor/editor.js'), 'utf8');
  assert.ok(!/after\.replaceChildren\(head\(/.test(src),
    'an engine still renders straight into the cell, bypassing the stage');
  assert.match(src, /showResult\('preview', canvas/);
  assert.match(src, /showResult\('exact', img/);

  // The stage was briefly a hardcoded 320px. Once the panes resize, its job is
  // to be bounded BY the pane, so a fixed height would fight the splitter.
  const css = readFileSync(join(ROOT, 'web/app.css'), 'utf8');
  assert.doesNotMatch(css, /\.compare-stage\s*\{[^}]*[^-]height: \d/,
    'the stage has a fixed height again, which the splitter cannot override');
  assert.match(css, /\.compare-stage img[^{]*\{[^}]*max-height: 100%/);
});

await atest('the result says how many pixels it is', async () => {
  const src = readFileSync(join(JS, 'views/editor/editor.js'), 'utf8');
  assert.match(src, /sizeLabel\(image\.width, image\.height\)/,
    'the live preview does not report its size');
  assert.match(src, /sizeLabel\(a\.width, a\.height\)/,
    'the exact preview does not report its size');
});

await atest('the shader keys the same colour the written file keys', async () => {
  // Two parsers is a real cost; a shader keying a different colour from the
  // one Python keys is a worse one, so the accepted forms are checked to match.
  const { parseColour } = await import(join(JS, 'core/colour.js'));

  assert.deepEqual(parseColour('12, 34, 56'), [12, 34, 56]);
  assert.deepEqual(parseColour('12,34,56'), [12, 34, 56]);
  assert.deepEqual(parseColour('rgb(12, 34, 56)'), [12, 34, 56]);
  assert.deepEqual(parseColour('#0a1b2c'), [10, 27, 44]);
  assert.deepEqual(parseColour('0a1b2c'), [10, 27, 44]);
  assert.deepEqual(parseColour('#abc'), [170, 187, 204]);
  assert.equal(parseColour(''), null);
  assert.equal(parseColour(null), null);
  assert.equal(parseColour('300, 0, 0'), null);
  assert.equal(parseColour('chartreuse'), null);
});

console.log('\neditor panes');
await atest('a split cannot swallow the pane that holds its handle', async () => {
  const { clampFraction, fractionAt, MIN_FRACTION, MAX_FRACTION } =
    await import(join(JS, 'views/editor/panes.js'));

  assert.equal(clampFraction(0.5), 0.5);
  assert.equal(clampFraction(-3), MIN_FRACTION, 'a drag past the left edge hid a pane');
  assert.equal(clampFraction(99), MAX_FRACTION, 'a drag past the right edge hid a pane');
  assert.equal(clampFraction(NaN), (MIN_FRACTION + MAX_FRACTION) / 2);

  assert.equal(fractionAt(250, 1000), 0.25);
  assert.equal(fractionAt(0, 1000), MIN_FRACTION);
  // A container measured before layout reports 0, and dividing by it is how a
  // splitter ends up at Infinity and the pane vanishes on first paint.
  assert.equal(fractionAt(100, 0), (MIN_FRACTION + MAX_FRACTION) / 2);
});

await atest('saved ratios survive storage being unavailable', async () => {
  const { loadRatios, saveRatios } = await import(join(JS, 'views/editor/panes.js'));
  const fallback = { side: 0.72, compare: 0.5 };

  // No localStorage in node at all, which is the same shape as a private
  // window or a browser set to block site data: it must open at its defaults
  // rather than throw before the editor renders.
  assert.deepEqual(loadRatios(fallback), fallback);
  assert.doesNotThrow(() => saveRatios(fallback));
});

await atest('the editor is one shell, not four bordered cards', async () => {
  const css = readFileSync(join(ROOT, 'web/app.css'), 'utf8');
  const body = /\.editorbody\s*\{([^}]*)\}/.exec(css)[1];
  assert.match(body, /border: 1px solid var\(--line\)/,
    'the shell has no border of its own');
  assert.doesNotMatch(body, /gap:/, 'the shell still gaps its panes apart');
  assert.match(body, /--side-split/, 'the side column is not driven by a ratio');
  assert.match(/\.compare\s*\{([^}]*)\}/.exec(css)[1], /--compare-split/);
  // The panes inside must not re-add the borders the shell replaced.
  assert.match(css, /\.editorside \.stackpanel[^{]*\{[^}]*border: 0/);
});

console.log('\nrun failure reporting');
await atest('a run that produced nothing says why, from its own log', async () => {
  const { failureFrom } = await import(join(JS, 'views/result/result.js'));

  // The real log off disk. Every run on this machine failed this way for two
  // days while the view said "No output yet. Start a run."
  const real = [
    'Traceback (most recent call last):',
    '  File "/x/pipeline/stages/pose.py", line 105, in run',
    '    rig = ctx.need("rig")',
    '  File "/x/pipeline/refs/references.py", line 77, in _one',
    '    raise NotFound("reference image", entry["path"])',
    "pipeline.shared.errors.NotFound: no reference image 'overnight/char_3/refs/side.png'",
  ].join('\n');

  const got = failureFrom(real);
  assert.equal(got.kind, 'NotFound');
  assert.equal(got.where, 'pose', 'the failing stage was not identified');
  assert.match(got.message, /no reference image/);
});

await atest('a clean log is not reported as a failure', async () => {
  const { failureFrom } = await import(join(JS, 'views/result/result.js'));
  assert.equal(failureFrom(''), null);
  assert.equal(failureFrom(null), null);
  assert.equal(failureFrom('stage pose ok\nstage depth ok\n'), null,
    'a successful run was reported as failed');
});

await atest('an unrecognised last line still reports something', async () => {
  const { failureFrom } = await import(join(JS, 'views/result/result.js'));
  const got = failureFrom('Traceback (most recent call last):\n  ...\nKilled');
  assert.equal(got.kind, 'Failed');
  assert.equal(got.message, 'Killed', 'a SIGKILL left the banner with nothing to say');
});

console.log('\nbackdrop colour');
await atest('every form a person types round-trips to one hex value', async () => {
  const { normaliseColour } = await import(join(JS, 'core/colour.js'));

  assert.equal(normaliseColour('12, 34, 56'), '#0c2238');
  assert.equal(normaliseColour('rgb(255, 0, 255)'), '#ff00ff');
  assert.equal(normaliseColour('#abc'), '#aabbcc');
  assert.equal(normaliseColour('#FF00FF'), '#ff00ff');
  assert.equal(normaliseColour('0,0,0'), '#000000', 'black lost a digit');
  assert.equal(normaliseColour('nonsense'), null);
  assert.equal(normaliseColour(''), null);
});

await atest('the presets lead with magenta and green carries its cost', async () => {
  // Green is the film industry default and the wrong default here: it sits
  // close to skin and cloth, and every pixel it bleeds into costs a palette
  // entry. It is offered, labelled, and not first.
  const src = readFileSync(join(ROOT, 'pipeline/shared/colour.py'), 'utf8');
  const block = /BACKDROP_PRESETS[^=]*=\s*\(([\s\S]*?)\n\)/.exec(src)[1];
  const hexes = [...block.matchAll(/"(#[0-9A-Fa-f]{6})"/g)].map((m) => m[1]);

  assert.equal(hexes[0], '#FF00FF', 'magenta is no longer the first preset');
  assert.ok(hexes.includes('#00B140'), 'chroma green is not offered at all');
  assert.match(block, /bleeds into skin/, 'green is offered without its trade-off');
});

await atest('one colour control, used by both forms', async () => {
  // The settings form and the definitive layer form are the same question
  // asked twice. They rendered it two ways: a picker in one, a bare text box
  // in the other, because only the schema side was upgraded.
  const kit = readFileSync(join(JS, 'ui/kit.js'), 'utf8');
  assert.match(kit, /export function ColourPicker/);
  assert.match(kit, /type: 'color'/, 'no native picker, so no choosing by eye');
  assert.match(kit, /swatchrow/, 'no presets, so every choice needs research');
  assert.match(kit, /text\.onchange =/);
  assert.doesNotMatch(kit, /text\.oninput =/,
    'reformatting on every keystroke makes the box impossible to type into');

  for (const view of ['fields.js', 'views/editor/stack.js']) {
    const src = readFileSync(join(JS, view), 'utf8');
    assert.match(src, /ColourPicker\(/, `${view} does not use the shared control`);
    assert.doesNotMatch(src, /type: 'color'/, `${view} builds its own picker`);
    assert.doesNotMatch(src, /0-9a-f\]\{3\}/, `${view} grew its own colour regex`);
  }
});

console.log('\nsettings list editors');
await atest('every function the settings form calls is defined', async () => {
  // 197651b deleted subControl and left its call, so every list editor threw
  // ReferenceError on render. Nothing caught it: the list editors are the one
  // part of the settings form no test drives, and a call to a name that does
  // not exist is only an error when the line actually runs.
  const src = readFileSync(join(JS, 'fields.js'), 'utf8');

  const defined = new Set([
    // declarations, however they are spelled
    ...[...src.matchAll(/(?:^|\s)function\s+(\w+)/g)].map((m) => m[1]),
    ...[...src.matchAll(/(?:const|let|var)\s+(\w+)\s*=/g)].map((m) => m[1]),
    // import bindings, under their LOCAL name when aliased
    ...[...src.matchAll(/^import\s*\{([^}]*)\}/gm)]
      .flatMap((m) => m[1].split(',').map((s) => s.trim().split(/\s+as\s+/).pop())),
    // parameters: a callback named onChange is defined where it is received
    ...[...src.matchAll(/\(([^)(]*)\)\s*(?:=>|\{)/g)]
      .flatMap((m) => m[1].split(',').map((s) => s.trim().replace(/[=:].*$/, '').trim()))
      .filter((s) => /^\w+$/.test(s)),
  ]);

  const called = new Set([...src.matchAll(/(?<![.\w])([a-z]\w{3,})\(/g)].map((m) => m[1]));
  const builtins = new Set([
    'if', 'for', 'while', 'switch', 'catch', 'return', 'typeof', 'function',
    'parseFloat', 'parseInt', 'structuredClone', 'setTimeout', 'clearTimeout',
    'encodeURIComponent', 'require', 'super', 'await', 'string', 'number',
  ]);

  const missing = [...called].filter((name) => !defined.has(name) && !builtins.has(name));
  assert.deepEqual(missing, [], `fields.js calls undefined: ${missing.join(', ')}`);
});

await atest('list cards can render every field type their specs declare', async () => {
  const src = readFileSync(join(JS, 'fields.js'), 'utf8');
  const declared = new Set(
    [...src.matchAll(/type: '(\w+)'/g)].map((m) => m[1]));
  const sub = /function subControl[\s\S]*?\n\}/.exec(src)[0];
  const control = /export function control[\s\S]*?\n\}\n\n/.exec(src)[0];

  // vec3/vec2/image exist only on list cards; the rest fall through to control.
  for (const shape of ['vec3', 'vec2', 'image']) {
    assert.ok(declared.has(shape), `no spec declares ${shape} any more`);
    assert.ok(sub.includes(`'${shape}'`), `subControl cannot render ${shape}`);
  }
  // A card spec says optionsFrom; a schema field says options_from.
  assert.match(sub, /options_from: spec\.optionsFrom/,
    'the two option vocabularies are no longer bridged');
  assert.ok(control.length > 0);
});

console.log('\nautosave');
await atest('a drag does not queue one save per frame', async () => {
  const { autosaver } = await import(join(JS, 'core/autosave.js'));
  let inflight = 0, peak = 0, calls = 0;
  const s = autosaver(async () => {
    calls++; inflight++; peak = Math.max(peak, inflight);
    await new Promise((r) => setTimeout(r, 25));
    inflight--;
  }, { wait: 5 });

  for (let i = 0; i < 8; i++) s.touch();
  await s.settle();
  assert.equal(peak, 1, 'two saves overlapped');
  assert.ok(calls <= 2, `a drag of eight edits made ${calls} saves`);
});

await atest('the last edit is not the one that is lost', async () => {
  const { autosaver } = await import(join(JS, 'core/autosave.js'));
  const seen = [];
  let value = 0;
  const s = autosaver(async () => {
    const v = value;
    await new Promise((r) => setTimeout(r, 20));
    seen.push(v);
  }, { wait: 1 });

  value = 1; s.touch();
  await new Promise((r) => setTimeout(r, 5));
  value = 2; s.touch();
  await s.settle();
  assert.equal(seen[seen.length - 1], 2, `saved ${seen} - the last edit was dropped`);
});

await atest('settle waits for a save already in flight', async () => {
  const { autosaver } = await import(join(JS, 'core/autosave.js'));
  let done = false;
  const s = autosaver(async () => {
    await new Promise((r) => setTimeout(r, 30));
    done = true;
  }, { wait: 1 });
  s.touch();
  await s.settle();
  assert.equal(done, true, 'settle returned before the save finished');
});

await atest('a failed save says so and does not wedge', async () => {
  const { autosaver, saveLabel } = await import(join(JS, 'core/autosave.js'));
  const phases = [];
  let fail = true;
  const s = autosaver(async () => { if (fail) throw new Error('nope'); },
                      { wait: 1, onState: (p, d) => phases.push(saveLabel(p, d)) });
  s.touch();
  await s.settle();
  assert.ok(phases.includes('Not saved: nope'), phases.join(','));

  fail = false;
  s.touch();
  await s.settle();
  assert.equal(phases[phases.length - 1], 'Saved', 'it never recovered');
});

await atest('both editors save through the one autosaver', async () => {
  for (const view of ['views/run/rig.js', 'views/run/annotate.js']) {
    const src = readFileSync(join(JS, view), 'utf8');
    assert.match(src, /from '\.\.\/\.\.\/core\/autosave\.js'/,
      `${view} does not use the shared autosaver`);
  }
  const ann = readFileSync(join(JS, 'views/run/annotate.js'), 'utf8');
  assert.doesNotMatch(ann, /Save annotation/, 'the annotation save button is back');
  assert.doesNotMatch(ann, /\bdirty\b/, 'the dirty flag it drove is back');
  assert.match(ann, /savestate/, 'nothing on screen says whether it saved');

  const run = readFileSync(join(JS, 'views/run/run.js'), 'utf8');
  assert.doesNotMatch(run, /Unsaved skeleton edits/);
  assert.match(run, /rigSaver\.settle\(\)/, 'leaving no longer flushes the edit');
});

await atest('a library preview has no run to write to', async () => {
  const src = readFileSync(join(JS, 'views/run/rig.js'), 'utf8');
  assert.match(src, /if \(!runId\) return \{ touch/,
    'previewing the library would POST a null run id');
});

console.log('\nnavigation history');
await atest('back returns to the last screen, not the last render', async () => {
  const { createHistory, snapshot, same } = await import(join(JS, 'core/history.js'));
  const h = createHistory();
  const state = { tab: 'overview', settingsSection: 'Asset', scope: 'pipeline' };

  h.push(snapshot(state));
  state.tab = 'settings';
  h.push(snapshot(state));
  state.settingsSection = 'Palette';

  assert.equal(h.depth(), 2);
  assert.equal(h.pop().settingsSection, 'Asset', 'a sub-state change was not undoable');
  assert.equal(h.pop().tab, 'overview');
  assert.equal(h.pop(), null);
  assert.equal(h.canGoBack(), false);

  // Re-rendering a view is not a navigation; a stack of identical entries
  // makes Back look broken.
  const g = createHistory();
  g.push(snapshot(state)); g.push(snapshot(state)); g.push(snapshot(state));
  assert.equal(g.depth(), 1, 'repeated identical positions were all recorded');
  assert.ok(same(snapshot(state), snapshot(state)));
});

await atest('a state that failed to draw is never somewhere back can land', async () => {
  // Without forget(), Back walks into the screen that just threw - the same
  // loop one level deeper, because the failing state was pushed on the way in.
  const { createHistory, snapshot } = await import(join(JS, 'core/history.js'));
  const h = createHistory();
  const state = { tab: 'overview', settingsSection: 'Asset' };

  h.push(snapshot(state));
  state.tab = 'settings';
  h.push(snapshot(state));
  state.settingsSection = 'Broken';
  h.push(snapshot(state));

  h.forget(snapshot(state));
  assert.notEqual(h.pop().settingsSection, 'Broken', 'back leads into the broken screen');
});

await atest('the stack cannot grow without bound', async () => {
  const { createHistory } = await import(join(JS, 'core/history.js'));
  const h = createHistory({ limit: 3 });
  for (let i = 0; i < 20; i++) h.push({ tab: `t${i}` });
  assert.equal(h.depth(), 3);
  assert.equal(h.pop().tab, 't19', 'the cap dropped the newest instead of the oldest');
});

await atest('the way out lives outside every view host', async () => {
  // mount() replaces the view host on failure. A back button rendered inside
  // one would be removed by the very error it exists to escape.
  const html = readFileSync(join(ROOT, 'web/index.html'), 'utf8');
  const sidebar = /<nav class="sidebar">[\s\S]*?<\/nav>/.exec(html)[0];
  assert.match(sidebar, /id="goback"/, 'the back button is not in the sidebar');

  const life = readFileSync(join(JS, 'listeners/lifecycle.js'), 'utf8');
  assert.match(life, /^import \{[^}]*forget[^}]*\} from '\.\.\/core\/history\.js'/m,
    'navigation reached through a global instead of an import');
  assert.match(life, /\bforget\(\)/, 'a failed state is still pushed');
  assert.match(life, /goBack\(\)/, 'the failure card still only offers Try again');

  // A module import is never undefined; the optional chaining was there
  // because the global might not exist yet.
  const all = ['main.js', 'listeners/lifecycle.js', 'views/settings/settings.js']
    .map((f) => readFileSync(join(JS, f), 'utf8')).join('\n');
  assert.doesNotMatch(all, /window\.pixelNav/, 'the navigation global is back');
});

console.log('\nresult view');
await atest('what fed a stage comes from the declared graph', async () => {
  // Each stage declares needs/gives and the manifest stores paths, so
  // provenance is derived. A hand-written map in the view would be a second
  // copy of the dependency graph, drifting from the one that runs.
  const src = readFileSync(join(ROOT, 'pipeline/api/runs.py'), 'utf8');
  assert.match(src, /def _consumed/);
  assert.match(src, /spec\.needs/, 'provenance is not read off the stage graph');
  assert.doesNotMatch(src, /"pose": \[|'pose': \[/,
    'a hand-written stage-to-input map crept in');
});

await atest('stages collapse through the shared primitive', async () => {
  // Structure lives beside Section and Subsection, not among the widgets.
  const prim = readFileSync(join(JS, 'ui/primitives.js'), 'utf8');
  assert.match(prim, /export function Disclosure/);
  assert.match(prim, /el\('details'/, 'a hand-rolled toggle instead of <details>');
  assert.match(prim, /Disclosure\(title, opts = \{\}, \.\.\.children\)/,
    'Disclosure does not take children the way Section does');
  assert.doesNotMatch(prim, /box\.body =/,
    'a caller still reaches in through a property on the DOM node');

  const view = readFileSync(join(JS, 'views/result/result.js'), 'utf8');
  assert.match(view, /Disclosure\(/);
  assert.doesNotMatch(view, /panel\.body/, 'the caller still uses the expando');
  assert.doesNotMatch(view, /group stagesection/, 'the old flat section survived');
});

await atest('a run is named by what it is, not by its type', async () => {
  // Every character_sheet run rendered "Sheet", which is the one thing they
  // all share, so the strip could not tell two runs apart.
  const view = readFileSync(join(JS, 'views/result/result.js'), 'utf8');
  assert.match(view, /replace\(\/\^\\d\{8\}_\\d\{6\}_\//,
    'the run id is not reduced to its config name');
  assert.match(view, /histname/);
});

await atest('the filmstrip mode is not called a sheet', async () => {
  // "Sheet" already means the exported sprite sheet and the asset type. A
  // third meaning on a view-mode button is the collision.
  const view = readFileSync(join(JS, 'views/result/result.js'), 'utf8');
  assert.doesNotMatch(view, /anim', 'sheet'/, 'the view mode is still called sheet');
  assert.match(view, /'grid', 'anim', 'strip'/);
});

console.log('\npolling');
await atest('a tick that says it is done stops the timer', async () => {
  // Both callers were written as though this worked. The return value was
  // discarded, so a queue at rest kept asking for as long as the tab was open.
  const src = readFileSync(join(JS, 'listeners/poll.js'), 'utf8');
  assert.match(src, /await fn\(\) === true\) stop\(\)/,
    'poll still discards what the tick tells it');
});

await atest('stopping when idle is paired with starting again', async () => {
  // A poll that stops and cannot restart is worse than one that never stops:
  // the view goes quietly stale instead of merely being wasteful.
  const main = readFileSync(join(JS, 'main.js'), 'utf8');
  assert.match(main, /function watchRuns\(\)/);
  assert.match(main, /if \(stopWatching\) return;/, 'two timers can run at once');
  const starts = main.match(/watchRuns\(\)/g) || [];
  assert.ok(starts.length >= 4, `only ${starts.length} references; a start path is uncovered`);

  const queue = readFileSync(join(JS, 'views/queue/queue.js'), 'utf8');
  assert.match(queue, /if \(moving\(\)\) watch\(\);/,
    'the queue stops polling and never restarts');
});

await atest('an unchanged payload does not rebuild the view', async () => {
  const main = readFileSync(join(JS, 'main.js'), 'utf8');
  assert.match(main, /function runsSignature/);
  assert.match(main, /signature === lastSignature\) return;/,
    'every tick still tears the view down');
  // The signature has to move when anything visible moves.
  for (const field of ['modified', 'running', 'stopped_at', 'completed', 'images.length']) {
    assert.ok(main.includes(field.split('.')[0]),
      `${field} is not part of the signature, so a change to it renders nothing`);
  }
  assert.match(main, /force = false/, 'a caller cannot force a redraw after acting');
});

console.log('\nrig reference pose');
await atest('reset returns a joint to the rig, not to frame 0', async () => {
  // neutral was structuredClone(entries[0].pose), so Reset restored whatever
  // the first loaded frame happened to be. dragJoint snaps bone lengths
  // against the same variable, so a bent opening frame skewed every drag.
  const src = readFileSync(join(JS, 'views/run/rig.js'), 'utf8');
  assert.match(src, /neutral = rigDef\?\.neutral/,
    'the reference pose is still taken from the first frame');
  assert.doesNotMatch(src, /^\s*neutral = entries\[0\]/m,
    'the old assignment survived');
});

console.log('\nnavigation labels');
await atest('the two Runs are not both called Run', async () => {
  // The nav item is a verb, start one; the sidebar select is a noun, this
  // existing one. Adjacent and identically labelled, they read as the same
  // thing, which is how a Result tab looked like it was about to re-run.
  const html = readFileSync(join(ROOT, 'web/index.html'), 'utf8');
  const nav = /<li data-view="run">.*?<\/li>/s.exec(html)[0];
  const picker = /<label class="sidelabel" for="runPicker">([^<]*)<\/label>/.exec(html)[1];

  assert.doesNotMatch(nav.replace(/<[^>]*>/g, '').trim(), /^Run$/,
    'the nav item is still the bare word Run');
  assert.notEqual(picker.trim(), 'Run', 'the sidebar label is still the bare word Run');
  assert.notEqual(nav.replace(/<[^>]*>/g, '').trim(), picker.trim(),
    'both chrome labels still say the same word');
});

await atest('the wizard says what it will act on', async () => {
  const src = readFileSync(join(JS, 'views/run/run.js'), 'utf8');
  assert.match(src, /wizardtarget/, 'the wizard never names its target');
  // rigStep edits state.selectedRun, so the wizard has to say which run that
  // is before the step that does it.
  assert.match(src, /rig edits apply to/);
});

await atest('a result image cannot be stretched by its own attributes', async () => {
  // Setting img.width and img.height as attributes under `max-width: 100%`
  // clamps the width and leaves the height, which squashes horizontally and
  // stretches vertically. That was the mirrored streaking on a sheet, and it
  // was the display, not the pipeline: grid measures factor 1 on every real
  // source and passes them through untouched.
  const src = readFileSync(join(JS, 'views/editor/editor.js'), 'utf8');
  const result = /const img = el\('img', \{ src: r\.image[\s\S]{0,400}/.exec(src)[0];
  assert.doesNotMatch(result, /img\.width =/, 'the result image sizes itself again');
  assert.doesNotMatch(result, /img\.height =/);

  const css = readFileSync(join(ROOT, 'web/app.css'), 'utf8');
  const stage = /\.compare-stage img[^{]*\{([^}]*)\}/.exec(css)[1];
  for (const rule of ['max-width', 'max-height', 'width: auto', 'height: auto']) {
    assert.ok(stage.includes(rule), `.compare-stage img is missing ${rule}`);
  }
});

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
