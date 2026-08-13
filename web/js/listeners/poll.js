/* An interval that owns its stop, skips a hidden tab, and never stacks ticks.
 * Two setIntervals used to outlive the views that started them. */
export function poll(fn, { every = 4000, immediate = true } = {}) {
  let stopped = false;
  let running = false;
  let timer = null;

  const tick = async () => {
    if (stopped || running) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    running = true;
    try {
      await fn();
    } catch (e) {
      // Usually the server restarting; the next tick retries.
      console.debug('poll failed:', e.message);
    } finally {
      running = false;
    }
  };

  if (immediate) tick();
  timer = setInterval(tick, every);

  return function stop() {
    stopped = true;
    clearInterval(timer);
    timer = null;
  };
}

/* Right for a cheap reaction to typing. Debouncing reduces how OFTEN
 * something runs and nothing about what one costs. */
export function debounce(fn, wait = 250) {
  let timer = null;
  const call = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  call.cancel = () => clearTimeout(timer);
  return call;
}
