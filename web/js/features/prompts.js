/* A style sheet's words, bound to the route that could always save them. Two
 * views show the same vocabulary and one had grown its own chip editor. */

import { api } from '../api.js';
import { showError } from '../core/errors.js';
import { Editable } from '../ui/index.js';
import { toast } from '../store.js';

const EMPTY = { groups: 'No vocabulary.', lines: 'No notes.' };
const SAVED = { groups: 'Vocabulary saved', lines: 'Notes saved' };

/** `kind` is 'groups' for the vocabulary or 'lines' for the notes. */
export function promptEditor(detail, kind, value, refresh) {
  return Editable(value, {
    kind,
    placeholder: '+ add',
    empty: EMPTY[kind],
    locked: kind === 'lines' && !detail.foldered
      ? `${detail.name} is a single YAML file, so it has nowhere to keep notes.`
      : '',
    onSave: async (next) => {
      try {
        await api.stylePrompts(detail.name,
                               kind === 'groups' ? next : null,
                               kind === 'lines' ? next : null);
        toast(SAVED[kind]);
        refresh(true);
        return true;
      } catch (e) { showError(e); return false; }
    },
  });
}
