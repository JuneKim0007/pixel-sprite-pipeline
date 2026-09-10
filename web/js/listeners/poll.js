// An interval that owns its stop, skips a hidden tab, and never stacks ticks.
export function poll(fn, { every = 4000, immediate = true } = {}) {
  let stopped = false;
  let running = false;
  let timer = null;

  const tick = async () => {
    if (stopped || running) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    running = true;
    try {
      // A tick that answers true has nothing left to watch.
      if (await fn() === true) stop();
    } catch (e) {
      // Usually the server restarting; the next tick retries.
      console.debug('poll failed:', e.message);
    } finally {
      running = false;
    }
  };

  function stop() {
    stopped = true;
    clearInterval(timer);
    timer = null;
  }

  if (immediate) tick();
  timer = setInterval(tick, every);
  return stop;
}

// Debouncing changes how often something runs, never what one run costs.
export function debounce(fn, wait = 250) {
  let timer = null;
  const call = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  call.cancel = () => clearTimeout(timer);
  return call;
}
