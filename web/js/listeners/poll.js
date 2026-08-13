/* An interval with an owner and a stop.
 *
 * There were two setIntervals in the codebase and neither was cancelled when
 * its view went away: leaving the result tab mid-playback left its timer
 * running for the life of the page, ticking against nodes that no longer
 * existed.
 *
 * This is the custom-hook idea without the hook. A poll returns its own stop,
 * so the caller cannot start one without holding the thing that ends it, and
 * the lifecycle can end it without knowing what it was for.
 *
 * Two behaviours the bare setInterval does not have:
 *
 *   overlap      A tick that is still running does not get a second one on
 *                top. A four-second poll against a request that takes six is
 *                otherwise a queue that only grows.
 *   visibility   A hidden tab does not poll. Nothing on screen is waiting for
 *                the answer, and this runs beside a GPU job that wants the
 *                machine more than a background tab does.
 */

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
      // A failed poll is usually the server restarting. The next tick retries,
      // and turning that into a visible error would make a restart look like a
      // crash.
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

/* Run `fn` after things settle, cancelling any pending run.
 *
 * The editor learned this the expensive way: debouncing reduces how OFTEN an
 * operation runs and does nothing about what one costs. It is right for a
 * cheap reaction to typing and wrong as a way to make an expensive one
 * tolerable - for that, ask before running it.
 */
export function debounce(fn, wait = 250) {
  let timer = null;
  const call = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  call.cancel = () => clearTimeout(timer);
  return call;
}
