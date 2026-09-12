import { api } from '../../api.js';
import { el, kids } from '../../core/dom.js';
import { showError } from '../../core/errors.js';
import { state, toast } from '../../store.js';
import { confirmDialog } from '../../ui/dialog.js';
import { Button, Mini } from '../../ui/index.js';

function describe(scope) {
  return scope.count
    ? `${scope.count} ${scope.count === 1 ? 'item' : 'items'}`
    : 'nothing to remove';
}

async function run(scopes, title, body, after) {
  const { ok } = await confirmDialog({ title, body, confirmLabel: 'Delete' });
  if (!ok) return;
  try {
    const { removed } = await api.wipe(scopes);
    const total = Object.values(removed).reduce((a, b) => a + b, 0);
    toast(`Removed ${total} ${total === 1 ? 'item' : 'items'}`);
    // The run this page was pointed at may be one of them.
    state.selectedRun = null;
    state.runDir = null;
    after();
  } catch (e) {
    showError(e);
  }
}

export function housekeepingSection(host, rerender) {
  const box = el('div', { className: 'fields' });

  api.housekeeping().then(({ scopes }) => {
    const rows = scopes.map((scope) => {
      const button = Button('Delete', {
        variant: 'ghost', disabled: !scope.count,
        title: scope.note,
      });
      button.onclick = () => run([scope.name], `Delete ${scope.label.toLowerCase()}?`,
                                 `${scope.note}\n\n${describe(scope)}.`, rerender);
      return el('div', { className: 'houserow' },
        el('div', {},
          el('b', { textContent: scope.label }),
          Mini(`${scope.note} — ${describe(scope)}`)),
        button);
    });

    const total = scopes.reduce((n, s) => n + s.count, 0);
    const all = Button('Delete everything', { variant: 'danger', disabled: !total });
    all.onclick = () => run(scopes.map((s) => s.name), 'Delete everything?',
      'Every output, log, export and style history, in one go.\n\n'
      + `${total} ${total === 1 ? 'item' : 'items'} across `
      + `${scopes.filter((s) => s.count).length} of ${scopes.length} scopes.`,
      rerender);

    box.replaceChildren(...kids(
      Mini('Removing is immediate and cannot be undone. A wipe is refused '
           + 'while a run is going, because it would delete what that run is '
           + 'writing.'),
      ...rows,
      el('div', { className: 'houserow wipeall' },
        el('div', {},
          el('b', { textContent: 'Everything' }),
          Mini('All of the above, in one operation')),
        all)));
  }).catch(showError);

  return box;
}
