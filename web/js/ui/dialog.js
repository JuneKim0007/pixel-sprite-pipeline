/* Modal dialogs. The card, and the three that carry no domain knowledge.
 *
 * `dialog` is the card every modal in the app is built from. Four of them had
 * hand-rolled the same eight things - a promise, a close that removes and
 * resolves, a backdrop that dismisses, the modal/modal-card/h2/modal-actions
 * tree, and the append to body. */

import { api } from '../api.js';
import { el } from '../core/dom.js';

/** `build(close)` returns `{ content, actions, dismiss?, opened? }`. */
export function dialog({ title, wide = false }, build) {
  return new Promise((resolve) => {
    let node;
    const close = (value) => { node.remove(); resolve(value); };
    const { content, actions, dismiss, opened } = build(close);

    node = el('div', { className: 'modal' },
      el('div', { className: `modal-card${wide ? ' wide' : ''}` },
        el('h2', { textContent: title }),
        ...[].concat(content).filter(Boolean),
        el('div', { className: 'modal-actions' }, ...[].concat(actions))));

    node.onclick = (e) => { if (e.target === node) (dismiss || (() => close(null)))(); };
    document.body.append(node);
    opened?.();
  });
}

/** One labelled control, with an optional config path and anything below it. */
export const field = (label, control, path = null, ...below) =>
  el('div', { className: 'field' },
    el('div', { className: 'field-top stacked' },
      el('div', {},
        el('label', { textContent: label }),
        path ? el('div', { className: 'path', textContent: path }) : null),
      el('div', { className: 'control-wrap' }, control)),
    ...below.filter(Boolean));

export function confirmDialog({ title, body, confirmLabel = 'Continue', rememberKey = null }) {
  return dialog({ title }, (close) => {
    const remember = rememberKey ? el('label', { className: 'chk' },
      el('input', { type: 'checkbox' }), " Don't show this again") : null;
    const answer = (ok) => close(
      { ok, remember: !!remember?.querySelector('input')?.checked });

    const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });
    const ok = el('button', { className: 'btn primary', textContent: confirmLabel });
    cancel.onclick = () => answer(false);
    ok.onclick = () => answer(true);

    return {
      content: [el('div', { className: 'modal-body', innerHTML: body }), remember],
      actions: [cancel, ok],
      dismiss: () => answer(false),
      opened: () => ok.focus(),
    };
  });
}

export function lightbox(src, caption = '') {
  const box = el('div', { className: 'lightbox' },
    el('div', {},
      el('img', { src }),
      el('div', { className: 'cap', textContent: caption })));
  box.onclick = () => box.remove();
  document.body.append(box);
}

export function browseDialog(startPath = '', imagesOnly = false) {
  return dialog({ title: imagesOnly ? 'Select images' : 'Select a folder', wide: true },
    (close) => {
      const listing = el('div', { className: 'browser' });
      const crumb = el('div', { className: 'crumb mono' });
      const chosen = new Set();
      let currentDir = startPath;

      const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });
      const ok = el('button', {
        className: 'btn primary', textContent: imagesOnly ? 'Select' : 'Use folder',
      });
      cancel.onclick = () => close(null);
      ok.onclick = () => close(imagesOnly ? [...chosen] : currentDir);

      const load = async (path) => {
        try {
          const data = await api.browse(path, imagesOnly);
          currentDir = data.dir;
          crumb.textContent = data.dir;
          listing.replaceChildren();

          if (data.parent) {
            const up = el('div', { className: 'browse-row dir' }, '\u2934  ..');
            up.onclick = () => load(data.parent);
            listing.append(up);
          }
          for (const item of data.entries) {
            const row = el('div', { className: `browse-row ${item.is_dir ? 'dir' : ''}` },
              item.is_dir ? '\ud83d\udcc1  ' : '',
              item.is_image ? el('img', { className: 'minithumb', src: api.fileUrl(item.path) }) : null,
              el('span', { textContent: item.name }));
            if (item.is_dir) row.onclick = () => load(item.path);
            else if (imagesOnly) {
              row.onclick = () => {
                row.classList.toggle('sel');
                chosen.has(item.path) ? chosen.delete(item.path) : chosen.add(item.path);
              };
            }
            listing.append(row);
          }
        } catch (e) {
          listing.replaceChildren(el('p', { className: 'empty', textContent: e.message }));
        }
      };

      return {
        content: [crumb, listing],
        actions: [cancel, ok],
        opened: () => load(startPath),
      };
    });
}
