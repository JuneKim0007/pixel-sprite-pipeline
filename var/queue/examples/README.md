# Writing jobs

Drop a JSON file in `queue/pending/` and run `make autopilot`.

```json
{
  "config": "character_sheet",     // a file in configs/
  "name": "bestiary",              // used in the run id
  "matrix": {                      // expands into one job per combination
    "rig": ["humanoid", "dragon"],
    "canonical.seed": [1234, 5678]
  },
  "overrides": {                   // dotted config paths
    "subject": "a fantasy creature"
  },
  "needs": ["inputs/ref.png"],     // held, not failed, until these exist
  "then": [                        // queued after this job succeeds
    { "config": "knight_attack", "name": "idle" }
  ]
}
```

The example above is **10 jobs** (5 rigs × 2 seeds), each of which queues an
animation afterwards — 20 runs from one file.

## What happens when something goes wrong

Job states are directories, so `ls queue/failed` is the whole status report.

| Situation | Result |
|---|---|
| Config error, unknown rig, unrunnable stage order | `failed/` immediately, with `.error.txt`. No retry — waiting cannot help |
| A file or parent run it needs does not exist yet | `held/`, retried every 10 minutes |
| ComfyUI or Ollama is down | The worker **pauses**. No job is blamed |
| Generation error, OOM, timeout | One retry, then `failed/` |
| 5 failures in a row | The worker stops rather than draining the queue |

That last pair is the important one. Every stage rejects a missing ComfyUI in
about a millisecond, so a worker that treated an outage as a job error would
empty a 200-job queue into `failed/` in well under a second. Measured, then
guarded against.

Chained jobs inherit **by reference**: the child points at the parent's finished
run rather than regenerating its canonical, so an animation is guaranteed to
match the sheet it came from and does not spend two GPU-minutes rediscovering
it.
