# The Definitive Layer diagnostic harness

`tools/definitive_trace.py` runs one image through one layer at a time and
records what the machine did. It exists because the failure under investigation
— the host restarting during a Definitive Layer run — leaves no Python
traceback, so the evidence has to be collected from outside the work.

It does not run the pipeline, start the server, touch ComfyUI, or use a process
pool. It changes nothing in `pipeline/`.

```
tools/definitive_trace.py --list
tools/definitive_trace.py --layer palette --size 512 --colours 24
tools/definitive_trace.py --all --input library/refs/knight_front.png --size 512
tools/definitive_trace.py --all --size 64 --log run.log --json run.json
tools/definitive_trace.py --all --size 128 --pace 0.5 --drop-caches --use-cache
```

## Stepping the stack instead of running it

Layers run in milliseconds. Back to back, their costs land inside one sampling
interval and read as a single figure, so a rise cannot be attributed to the
layer that caused it. `--pace SECONDS` stands still after each layer, and again
after popping caches, which puts a flat stretch on either side of every step.

`--drop-caches` empties the prepare and snapshot caches between layers, so
nothing one layer measured is reused by the next. On its own that pops an empty
cache: `run_layer` calls `prepare` directly, which is what isolates a layer's
own cost but is not the path production takes. `--use-cache` routes the prepare
through the same `prepare_for` that `apply_stack` uses, so the cache fills and
the popping means something.

The cache's byte column reads 0.00MB even when it holds entries. That is not
the harness: `Cache._size` counts any dict as 64 bytes regardless of contents,
which was measured 2026-09-10 and is an underestimate that does not matter -
the two prepare results are a list of 24 colours and three scalars,
and prepare results are dicts. The entry count is real; the byte figure is not.
Prepare results are small today, so the 8 MB byte cap has never bound - only
the 64-entry cap has.

## Why it is shaped this way

**Thread caps are set before numpy is imported.** The BLAS backends read
`OMP_NUM_THREADS` and friends at load time; setting them afterwards does
nothing. The harness sets them in the first statements of the file, above every
other import.

**The safety gate is a refusal, not an abort.** A single numpy expression can
cross from zero to twelve gigabytes inside one C call with no interpreter yield
point, so no sampler can react in time. The harness therefore predicts the peak
arithmetically and declines to execute anything at or above `MAX_PREDICTED_GB`.
The live sampler is a second net, never the thing being relied on.

The comparison is `>=`, not `>`. Measured 2026-09-08: 512px at K=256 predicts
exactly 1.0000 GB, `>` admitted it, and it ran to 1152.5 MB resident. A boundary
that admits its own limit is not a limit.

**Retention is reported as traced bytes, not an object count.** Numeric-dtype
ndarrays hold no Python object references, so `gc.is_tracked()` is false for
them and `gc.get_objects()` never returns them. A gc-based tally reads zero
while hundreds of megabytes are live. `tracemalloc` does see them — measured
4.19 MB traced for a 4 MB array — so traced memory is the number that means
something.

**The log is fsynced per line.** If the host dies mid-run, the last line names
the operation that was in flight. That is the only artifact that survives an
unclean restart, and it is the reason the harness writes a log at all.

## What it measured

Host: Mac16,1, Apple M4, 10 cores, 16 GB unified, macOS 15.5 (24F74).
Single-threaded throughout; no subprocess was created by any run.

### Palette memory and time were linear in pixels x colours

Measured before 1601d89 chunked it. Kept because it is the evidence that
justified the change, not a description of what the code does now.

512x512 = 262,144 px, palette alone:

| K | `N*K*16` | traced | overhead | peak RSS | `generate_palette` |
|---:|---:|---:|---:|---:|---:|
| 8 | 33.55 MB | 43.13 MB | +9.58 MB | 137.9 MB | 0.598 s |
| 24 | 100.66 MB | 109.92 MB | +9.26 MB | 205.3 MB | 1.343 s |
| 64 | 268.44 MB | 277.69 MB | +9.25 MB | 351.5 MB | 3.259 s |
| 128 | 536.87 MB | 545.99 MB | +9.12 MB | 619.4 MB | 6.234 s |
| 256 | 1073.74 MB | refused | — | — | not executed |

`traced = N*K*16 + 9.2 MB`, with the overhead constant to within 0.5 MB across
a 16x range of K. Time is likewise linear: about 1.79e-7 seconds per pixel per
colour, single-threaded.

The allocation is the broadcast in `generate_palette`:

```python
labels = ((feats[:, None, :] - c[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
```

`feats` is every pixel, not every unique colour, and the k-means loop runs it
twelve times. `apply_fixed_palette`, two functions below, chunked the same shape
of work through `limits.get("colour_chunk")`; this call site did not.

Since 1601d89 it does. The assignment runs a bounded block at a time and the
cost stopped following the colour count: measured peak 2.36 MB at K=8 and
2.37 MB at K=128, where unchunked the same sweep went 8.39 MB to 134.22 MB.
What remains is a flat ~36 bytes per pixel of genuinely O(N) work.

`admit()` cannot see it: the palette layer declares no `magnify`, so its
projected growth is 1.0. `BYTES_PER_OUTPUT_PIXEL = 24` budgets six live copies
at four bytes, while k-means uses `K*16` bytes per pixel — 384 bytes at the
default K of 24, sixteen times the budgeted figure.

### Grid does not reduce real diffusion output

Synthetic gradient content measures block size 16 and reduces 512x512 to 31x31,
which makes the palette layer look harmless. Real images do not behave that way:

| Source | measured factor | palette receives | reduction |
|---|---:|---:|---:|
| `knight_front.png` (832x1216) | 2 | 255x255 | 4x |
| `red_hanfu_swordswoman.png` (512x768) | 3 | 170x170 | 9x |
| `archer_dynamic.png` (584x779) | 1 | 512x512 | none |
| `_bigcheck.png` (1024x1024) | 1 | 512x512 | none |

On two of four real sources the image passes through untouched and the palette
layer sees full resolution. Any measurement taken on synthetic content
overstates how much grid protects the layers after it.

### Where the time goes on real content

Full stack, `archer_dynamic.png` centre-cropped to 512x512, K=24, factor 1:

| layer | time | output |
|---|---:|---|
| curves | 0.000 s | 512x512 RGBA |
| grid | 0.024 s | 512x512 RGBA (no reduction) |
| palette | 1.212 s | 512x512 RGB |
| background | 1.419 s | 512x512 RGBA |
| scale | 0.000 s | 512x512 RGBA |

Peak traced 110.26 MB, peak RSS 198.6 MB, 3.58 s wall.

`background` is a Python-level flood fill (`_flood`), so its cost is interpreter
time holding the GIL rather than native work.

`find_phase` inside grid was O(factor^2 x pixels) and the dominant cost on
synthetic content at factor 16 - 763 ms at the 384 px preview size. fbaaa79
replaced the scan with integral images and 758823c brought its memory back in
line; it is now 16.7 ms at that factor and no longer follows the factor at all.

### Extrapolation, not measurement

From the two fits above, at configurations the admission ceiling currently
allows and the harness refuses to run:

| configuration | predicted memory | predicted `generate_palette` |
|---|---:|---:|
| 4096x4096, K=64 | 17.2 GB | 192 s |
| 35.79 MP ceiling, K=24 | 13.7 GB | 154 s |

Both exceed 16 GB of physical memory or come close to it. These figures are
arithmetic from measured laws. They have not been executed and should not be.

They no longer describe the shipped code: since 1601d89 the palette layer's
peak does not follow the colour count, so the K=64 and K=256 rows are history.

An earlier version of this section also said the runtimes exceed the
120-second userspace watchdog threshold "while holding the GIL", and drew a
causal line from that to the host restarting. That line is withdrawn. The
watchdog monitors WindowServer's check-ins, not Python's runtime, and the GIL
is process-local - it serialises threads inside one interpreter and cannot
deprive another process of CPU. How long a layer runs and how long WindowServer
misses check-ins are unrelated quantities. The reported failures cluster on idle
and sleep rather than load, which matches this host's own stackshot
(`displayState: OFF`, mid sleep-cycle) and does not match compute.

## What has not been established

No shutdown occurred during any harness run. The host has 26 days of continuous
uptime across roughly twenty runs of the code path under suspicion, with system
memory pressure reading normal throughout.

The only recorded panic on this machine is 2026-08-13 10:32, a userspace
watchdog timeout on WindowServer. Its full backtrace file is referenced by the
summary but absent from disk, and the unified log's retention begins 2026-08-25,
twelve days after the event. No pipeline run artifact exists from that date.
There is therefore no evidence connecting any recorded shutdown to the
Definitive Layer, and the harness has not reproduced one.

What the harness establishes is narrower and worth stating exactly: the palette
layer has an unchunked allocation whose size and duration are linear in pixels
times colours, grid does not bound it on real input, and the product reaches
both the memory limit and the watchdog threshold at image sizes the editor
currently admits.

## A decompression bomb already on disk

`library/refs/_bigcheck_px.png` is 16384x16384 — 268,435,456 pixels, 1.00 GB
decoded as RGBA, 28 MB on disk. Pillow refuses to open it as a decompression
bomb. The `_px` suffix means the pipeline wrote it. `_open_bounded` in
`pipeline/api/editor.py` already catches `DecompressionBombError` and explains
that the file was probably written at a high zoom; this is an instance of
exactly that case, sitting in the reference folder.
