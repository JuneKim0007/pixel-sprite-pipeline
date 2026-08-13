# Front-end restructuring

A probe of `web/` as it stands, and the layout that follows. Written before
anything moves, so the reasoning survives the diff. Nothing here is applied
yet.

5,739 lines across 23 files. No build step, no framework, no `package.json`.

---

## 1. What the measurements say

### Every view re-renders itself whole, on every change

Six of the ten views define a `rerender` or `refresh` that calls their own
top-level render function:

```js
export function renderEditor(host) {
  const rerender = () => renderEditor(host);   // editor.js, and five others
```

A checkbox toggling means the whole panel is destroyed and rebuilt. That is why
the editor loses scroll position and focus, and it is the reason a form field
cannot hold transient state - there is nowhere for it to live across a redraw.

It is also why module-level `let` exists: state that has to survive a rerender
cannot be inside the function that rerenders. There are 14 such variables
outside `store.js`.

| file | module-level mutable state |
|---|---|
| `editor.js` | `catalogue stack selected source facts engine bitmap palette busy pending` |
| `run.js` | `rigDirty lastOpts` |
| `queue.js` | `showing timer` |
| `input.js` | `activeRole` |

### `store.js` is four unrelated things

| what | exports | imported by |
|---|---|---|
| DOM helpers | `el $ $$ escapeHtml` | 17 files want `el` |
| app state | `state draft clearDraft draftConfig loadConfig` | 12 want `state` |
| UI chrome | `toast confirmDialog lightbox` | 11 want `toast` |

Every view imports all of it to get one of the three. `el` is the single most
imported name in the codebase and it lives in a file called `store`.

### The listeners are ad hoc

Two `setInterval`s, three `setTimeout`s, nine `addEventListener`s, spread
across the views that happen to need them. There is one poll loop in `main.js`
that refreshes runs every four seconds, and a second in `result.js` driving
frame playback. Nothing owns them; nothing cancels them when a view is
replaced, which is why switching tabs during playback leaves the timer running.

### Three UI primitives exist and one is used

`ui/field.js`, `ui/card.js`, `ui/primitives.js` were written to make "every
control carries its (?)" structural. `BaseField` reached one caller after being
pointed at; `BaseCard` still has none.

---

## 2. About the React patterns

The `frontend-patterns` skill is written for React and Next.js. This front-end
has no build step, and that is deliberate: it means the UI runs anywhere with
nothing installed, and it is the property that makes the whole project portable.
Adding React would buy component ergonomics at the cost of the thing that makes
this runnable on someone else's laptop in one command.

So the patterns are worth translating rather than importing:

| React pattern | what it becomes here |
|---|---|
| Composition over inheritance | Small `el()`-returning functions taking children. Already how the code works. |
| Compound components | A view module exporting `render` plus its parts, sharing state through a closure rather than Context. |
| Custom hooks | Plain modules exporting behaviour: `usePolling` becomes `listeners/poll.js`. |
| Context + reducer | One `state` object plus explicit actions, which `store.js` half-does already. The missing half is that nothing subscribes. |
| `React.memo` | The thing that makes it unnecessary: render only the subtree that changed. |
| Error boundary | A `try/catch` around each view's render that shows the failure in place instead of blanking the tab. `main.js` does this only for boot. |
| Virtualisation | Genuinely needed for the run gallery once it passes a few hundred images. Not yet. |

The one React idea worth taking wholesale is **subscription**: a view declares
what state it reads, and re-renders when that changes rather than when someone
remembers to call `refresh()`.

---

## 3. The proposed shape

```
web/
  index.html
  css/
    tokens.css            colours, spacing, type scale
    base.css              reset, layout primitives
    components.css        one block per ui/ primitive
  js/
    core/                 no dependency on any view
      dom.js              el, $, $$, escapeHtml
      store.js            state, subscribe, actions
      api.js              every server call
      errors.js           one place that turns a failed response into a message

    listeners/            things that fire on their own
      poll.js             an owned interval: start, stop, and stopped on teardown
      shortcuts.js        keyboard, in one table rather than nine handlers
      lifecycle.js        view mount/unmount, so a view can clean up after itself

    ui/                   presentational, no knowledge of the domain
      field.js            BaseField and its subclasses
      card.js
      primitives.js       HelpTip, Section, Segmented, Toast
      dialog.js           confirm, lightbox

    views/                one folder per tab
      overview/
      input/
      run/
      result/
      styles/
      editor/
        index.js          render + teardown
        stack.js          the layer list
        gpu.js            the shader preview
      queue/
      settings/

    features/             domain logic with no DOM
      rig/                pose maths, joint naming, view resolution
      layers/             the editor's stack model
      config/             draft, diff against effective, validation

    main.js               boot, routing, mounting
```

Four rules, each checkable:

**`core/` depends on nothing.** Same rule the backend's `shared/` now has, and
the same test can check it. `el` moving out of `store.js` is most of the win on
its own: 17 files stop importing application state to build a `<div>`.

**`ui/` never imports `features/` or `views/`.** A primitive that knows what a
rig is has stopped being a primitive.

**`features/` never touches the DOM.** Pose maths is testable without a DOM
shim; today `views.js` mixes joint constants with rendering, and `rig.js` mixes
geometry with canvas drawing.

**A view owns its teardown.** `render(host)` returns a cleanup function, and
`lifecycle.js` calls it before mounting the next view. That is what stops the
result-tab playback timer running after you leave.

---

## 4. Where the real work is

Moving files is the easy half and mostly mechanical. Two changes are not.

### Subscription, so `refresh()` stops being manual

Today: mutate state, then remember to call the right render function.
Proposed: `store.set(path, value)` notifies whoever declared an interest.

```js
// core/store.js
const listeners = new Map();

export function subscribe(keys, fn) {
  const id = Symbol();
  listeners.set(id, { keys, fn });
  return () => listeners.delete(id);      // the unsubscribe IS the teardown
}
```

A view subscribes to `['runs']` and stops caring who changed them. This is what
removes both the module-level `let`s and the whole-panel rebuilds, because a
subscription can be scoped to a subtree.

### Partial rendering

`renderEditor(host)` rebuilding everything is the cause of the lost focus and
scroll. The fix is not a virtual DOM - it is that each piece of the view keeps
a reference to its own node and replaces only that:

```js
const panel = el('div');
const redrawPanel = () => panel.replaceChildren(...);   // not the whole view
```

The editor already does this for the result image and the facts bar. The stack
list and the form do not, which is exactly where editing feels bad.

---

## 5. Order of work

Each step is verifiable alone and none needs the next to be useful.

1. **`core/dom.js`.** Move `el $ $$ escapeHtml` out of `store.js`. Touches
   every file's imports and nothing else. The test is that the UI still boots.
2. **`core/store.js` gains `subscribe`.** Add it without removing anything;
   convert views one at a time.
3. **`listeners/`.** Give the two intervals an owner and a stop.
4. **`views/<tab>/`.** Move the ten view modules into folders. Mechanical once
   1 and 2 are done.
5. **`features/`.** Pull the pose maths out of `views.js` and `rig.js`, which
   is what makes it testable without a DOM.
6. **Partial rendering**, view by view, starting with the editor because it is
   where the cost is felt.

Steps 1, 3 and 4 are safe. Step 2 is where the design risk sits, and it should
land on one view first - `queue`, which is small and already polls - before
anything else adopts it.

---

## 6. How each claim can be rechecked

| claim | check |
|---|---|
| 14 module-level mutable variables | `grep -n "^let " web/js/*.js` |
| six views rebuild themselves whole | `grep -rn "rerender = \|refresh = " web/js` |
| `el` is imported by 17 files | count the destructured names from `store.js` |
| `core/` depends on nothing | walk the import graph, as `tests/test_api.py` does for `shared/` |
| a view cleans up after itself | leave the result tab mid-playback and assert the timer is gone |
