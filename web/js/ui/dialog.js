/* A file/folder picker, and nothing that knows what it is picking.
 *
 * It lived inside views/input, so Settings could only reach a Browse button by
 * importing another view. */

import { api } from '../api.js';
import { el } from '../core/dom.js';

export function browseDialog(startPath = '', imagesOnly = false) {
  return new Promise((resolve) => {
    const listing = el('div', { className: 'browser' });
    const crumb = el('div', { className: 'crumb mono' });
    const chosen = new Set();
    let currentDir = startPath;

    const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });
    const ok = el('button', { className: 'btn primary', textContent: imagesOnly ? 'Select' : 'Use folder' });

    const load = async (path) => {
      try {
        const data = await api.browse(path, imagesOnly);
        currentDir = data.dir;
        crumb.textContent = data.dir;
        listing.replaceChildren();

        if (data.parent) {
          const up = el('div', { className: 'browse-row dir' }, '⤴  ..');
          up.onclick = () => load(data.parent);
          listing.append(up);
        }
        for (const item of data.entries) {
          const row = el('div', { className: `browse-row ${item.is_dir ? 'dir' : ''}` },
            item.is_dir ? '📁  ' : '',
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

    const modal = el('div', { className: 'modal' },
      el('div', { className: 'modal-card wide' },
        el('h2', { textContent: imagesOnly ? 'Select images' : 'Select a folder' }),
        crumb, listing,
        el('div', { className: 'modal-actions' }, cancel, ok)));

    const close = (value) => { modal.remove(); resolve(value); };
    cancel.onclick = () => close(null);
    ok.onclick = () => close(imagesOnly ? [...chosen] : currentDir);
    modal.onclick = (e) => { if (e.target === modal) close(null); };
    document.body.append(modal);
    load(startPath);
  });
}
