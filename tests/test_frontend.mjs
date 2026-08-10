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
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const JS = join(ROOT, 'web/js');

const {
  VIEWS, JOINTS, LIMBS, projectPoint, unprojectX, snapToAnatomy, dragJoint,
  parentOf, subtree, visibleJoint, resolveView, dist3, SKELETON_TREE,
} = await import(join(JS, 'views.js'));

let pass = 0, fail = 0;
const test = (name, fn) => {
  try { fn(); console.log(`  ok    ${name}`); pass++; }
  catch (e) { console.log(`  FAIL  ${name}\n        ${e.message}`); fail++; }
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

console.log('\nstatic checks across every module');
for (const file of readdirSync(JS).filter((f) => f.endsWith('.js'))) {
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
    for (const m of src.matchAll(/^import \{([^}]+)\} from/gm)) {
      for (const raw of m[1].split(',')) {
        const name = raw.trim().split(/\s+as\s+/).pop().trim();
        if (!name) continue;
        // `$` and `$$` are not word characters, so \b never matches them.
        const esc = name.replace(/[$]/g, '\\$');
        const pattern = /^[A-Za-z_]/.test(name)
          ? new RegExp(`\\b${esc}\\b`, 'g')
          : new RegExp(`${esc}(?=\\s*\\()`, 'g');
        const uses = [...src.matchAll(pattern)].length;
        assert.ok(uses > 1, `${name} imported but unused`);
      }
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
const schemaSrc = readFileSync(join(ROOT, 'pipeline/schema.py'), 'utf8');
const settingsSrc = readFileSync(join(JS, 'settings.js'), 'utf8');

const declaredGroups = new Set(
  [...schemaSrc.matchAll(/"group":\s*"([^"]+)"/g)].map((m) => m[1]));

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
  const backend = [...readFileSync(join(ROOT, 'pipeline/references.py'), 'utf8')
    .match(/ROLES = \(([^)]+)\)/)[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  const frontend = [...readFileSync(join(JS, 'input.js'), 'utf8')
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
const { installDom } = await import(join(ROOT, 'tests/domshim.mjs'));
installDom();

const ui = await import(join(JS, 'ui/index.js'));
const { el } = await import(join(JS, 'store.js'));

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

console.log('\nview slots');
const inputSrc = readFileSync(join(JS, 'input.js'), 'utf8');
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
test('every schema field carries help, so no (?) is ever empty', async () => {
  // The BaseField marker makes a missing explanation visible rather than
  // invisible; this keeps the count from growing quietly.
  const src = readFileSync(join(ROOT, 'pipeline/schema.py'), 'utf8');
  const paths = [...src.matchAll(/\{"path":\s*"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(paths.length > 100, `only found ${paths.length} schema paths`);
});

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
