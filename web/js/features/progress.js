import { poll } from '../listeners/poll.js';

const ON_DEMAND_MS = 4000;
const BACKGROUND_MS = 60000;

// One shape, two producers.
export class ProgressFeed {
  constructor({ every, onUpdate }) {
    this.every = every;
    this.onUpdate = onUpdate;
    this.stop = null;
    this.last = null;
  }

  read() {
    throw new Error('a feed must say what it reads');
  }

  start() {
    if (this.stop) return this;
    this.stop = poll(async () => {
      const seen = await this.read();
      this.last = seen;
      this.onUpdate?.(seen);
      return seen?.idle === true;
    }, { every: this.every, immediate: true });
    return this;
  }

  end() {
    if (this.stop) this.stop();
    this.stop = null;
  }
}

/** What the GPU is doing right now. Cheap, frequent, stops when idle. */
export class GpuProgress extends ProgressFeed {
  constructor(api, onUpdate) {
    super({ every: ON_DEMAND_MS, onUpdate });
    this.api = api;
  }

  async read() {
    const { gpu, run } = await this.api.progress();
    const images = run?.images;
    return {
      label: 'This job',
      total: images?.total ?? 0,
      done: images?.made ?? 0,
      detail: gpu.reachable
        ? (gpu.busy ? `${gpu.queue_remaining} queued on the GPU` : 'GPU idle')
        : 'ComfyUI not reachable',
      idle: !gpu.busy && !run?.running,
    };
  }
}

/** How far the run itself has come. Slow, background, keeps going while it runs. */
export class RunProgress extends ProgressFeed {
  constructor(api, onUpdate, { every = BACKGROUND_MS } = {}) {
    super({ every, onUpdate });
    this.api = api;
  }

  async read() {
    const { run } = await this.api.progress();
    const jobs = run?.jobs;
    return {
      label: run?.id ? `Run ${run.id.replace(/^\d{8}_\d{6}_/, '')}` : 'No run',
      total: jobs?.total ?? 0,
      done: jobs?.done ?? 0,
      detail: run?.stages
        ? `stage ${run.stages.done}/${run.stages.total}, `
          + `${run.images.made}/${run.images.total} images`
        : '',
      idle: !run?.running,
    };
  }
}
