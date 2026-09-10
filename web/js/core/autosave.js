const DEFAULT_WAIT = 600;

export function autosaver(save, { wait = DEFAULT_WAIT, onState } = {}) {
  let timer = null;
  let saving = false;
  let again = false;

  const report = (phase, detail) => onState && onState(phase, detail);

  async function flush() {
    if (saving) { again = true; return; }
    saving = true;
    report('saving');
    try {
      await save();
      report('saved');
    } catch (e) {
      report('error', e.message);
    } finally {
      saving = false;
      if (again) { again = false; flush(); }
    }
  }

  return {
    touch() {
      report('pending');
      clearTimeout(timer);
      timer = setTimeout(flush, wait);
    },
    async settle() {
      clearTimeout(timer);
      await flush();
      while (saving) await new Promise((r) => setTimeout(r, 30));
    },
    cancel() {
      clearTimeout(timer);
    },
  };
}

export const SAVE_LABEL = {
  pending: 'Saving…',
  saving: 'Saving…',
  saved: 'Saved',
};

export function saveLabel(phase, detail) {
  return phase === 'error' ? `Not saved: ${detail}` : (SAVE_LABEL[phase] || '');
}
