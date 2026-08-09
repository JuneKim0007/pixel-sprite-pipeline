# Tileset generator — reuse survey

Written 2026-08-09 during the tree audit, before any tileset code exists.
Target: 64×64 tiles, Pokemon-style top-down terrain and objects. This maps
what the character pipeline already provides, what needs a parameter, and
what is character-specific and irrelevant. Every claim carries file:line
against the tree as of commit `1dd5105`.

The shortest correct framing: **a tileset is a third `module`, not a second
pipeline.** The codebase already switches surface and prompt templates on
`module: animation | character_sheet` (`pipeline/schema.py:590-599`
`fields_for()`, style sheets' `modules:` blocks e.g.
`styles/base_pixel.yaml:12-16`). `tileset` slots into that axis. The stage
registry (`pipeline/stage.py:206-226`) means new stages need only a class,
`@register`, and one import line in `pipeline/stages/__init__.py:7`.

---

## 1. Reusable as-is (no changes)

| What | Where | Why it transfers |
|---|---|---|
| Stage/Context/registry contract | `pipeline/stage.py:54-226` | Nothing in `Context` is character-specific except the helpers a tile stage simply won't call (`rig()`, `_measured_proportions()`). `stage_dir()` numbering, `artifacts` dependency checking, `require()` all transfer. |
| Runner: validate/plan/run, GPU serialisation | `pipeline/runner.py:41-198` | Resource policy (`stage.py:33-51`) is about the machine, not the subject. Tile pixelisation is CPU-parallel exactly like frame pixelisation. |
| ComfyUI client + graph builders | `pipeline/comfy.py:90-226` (`Client`, `Graph`), `comfy.py:229-327` (`base_graph`, `apply_ipadapter`, `apply_controlnet`), `comfy.py:360-381` (`sample_and_save`) | All subject-agnostic. `base_graph` already takes `models.checkpoint/pixel_lora/vae` overrides (comfy.py:253-278). `sample_and_save(batch=N)` gives free variant batching per terrain type. |
| img2img entry point | `comfy.encode_image` (comfy.py:349-357) | Kept deliberately for the "illustrate → pixelise" pass (DECISIONS.md "No img2img from an identity reference"). That pass is *exactly* how you'd convert an existing tile mockup or reference tile into the house style. |
| The whole pixelisation kernel | `pipeline/pixelize.py` — `find_phase` (:48), `reduce_blocks` (:69), `generate_palette` with 4 metrics (:207), `apply_fixed_palette` (:279), `curves` (:179), `load/save/extract_palette` (:106,151,128) | Operates on arrays, never on "a character". 64×64 falls out of existing knobs: 1024 canvas ÷ `factor: 16`, or 512 ÷ 8. `phase` locking (one grid origin for a whole set — `stages/palette.py:100-115`) is *more* important for tiles than for frames: adjacent tiles on one grid must not shimmer against each other. |
| Palette registry + LLM chooser | `pipeline/palettes.py` (`discover`, `choose`) | Already has an `environment/` group with `palettes/environment/cavern_damp.hex`. A terrain family locked to one `.hex` file is the tileset workflow. Selection-by-LLM, application-deterministic split (`stages/palette.py:182-211`) transfers unchanged. |
| Style-sheet layering | `pipeline/styles.py:74-155` (`Style`), `:200-224` (`_chain`/extends), `:274-329` (`layer`), `:226-272` (vocabulary/expand) | Sheets carry `settings:` per module and prompt templates per module (`styles/base_pixel.yaml`). `styles/pokemon_mono` (bold outlines, pinned pico8 palette) is practically the tileset house style already — it needs a `tileset:` entry in its `modules:` block, which is data, not code. Exemplar auto-discovery (`styles.py:92-108`) works for tile exemplars identically. |
| Queue + autopilot | `pipeline/queue.py`, `autopilot.py` | Subject-blind. `matrix` expansion (`queue.py`) is the natural way to enqueue {grass, sand, water, rock} × {plain, edge, corner} overnight. Held/failed/broken-vs-not-ready semantics (DECISIONS.md "Operations") apply verbatim. |
| Style exemplar conditioning | `references.style` role + `apply_ipadapter` at low weight (role default 0.35, `pipeline/references.py`) | "Make this tile look like these three tiles" is the same mechanism as "make this sprite look like these two Frieren frames". |
| Schema-driven UI | `pipeline/schema.py` field list + `modules:` scoping, `web/js/fields.js` | Declaring `"modules": ["tileset"]` on new fields makes them render with zero frontend work; `fields_for("tileset")` already filters. |

## 2. Reusable with a parameter / small change

| What | Where | Needed change |
|---|---|---|
| `PaletteStage` | `pipeline/stages/palette.py:38-133` | Two issues. (a) `requires = frozenset({"frames", "canonical"})` (:42-44) — either the tile-generation stage produces artifacts under the same names (cheapest; "canonical" = the anchor tile, "frames" = the tile variants), or `requires` needs loosening. (b) Background keying: `alpha_tol` is applied unconditionally (:57, passed at :121); a terrain tile is 100% subject and must keep every pixel. `pixelize()` already accepts `alpha_tol=None` (`pipeline/pixelize.py:426-439`) — the stage just needs `palette.alpha_tolerance: null` (or `off`) to map to `None` instead of the 14 default. Also `_extract_from_subject`'s keying (:152-164) must be skippable — for tiles, extract from the *whole* image; ironically its existing fallback branch (:158-163, "sprite may run to the canvas edge") is precisely the tile case. |
| `ExportStage` | `pipeline/stages/export.py:26-90` | Grid assembly + `sheet.json` cell geometry (:75-87) transfer. Two changes: cell size must be *pinned* (64×64), not `max(im.width…)` (:32-33) — off-size output should be an error for tiles, not silently accommodated; and `ALSO_JOIN = ("pose", "depth", "frames")` (:24) is character-flavoured but harmless (glob finds nothing). Tiles additionally want adjacency metadata in the JSON (see §4). |
| `CanonicalStage` as the "anchor tile" stage | `pipeline/stages/canonical.py` | The concept transfers whole: generate ONE anchor per terrain family at max quality; every variant inherits its palette (extraction) and look (IP-Adapter), exactly the frames-inherit-from-canonical recipe (DECISIONS.md "The canonical anchors every frame"). What does not transfer: `_anchor_view` (:21-46) and everything reading `pose.*`; the reference view-falloff matching (`references.match`) is meaningless for tiles. Probably a new thin `TileAnchorStage` reusing `base_graph`/`sample_and_save` rather than parameterising CanonicalStage — the view/pose plumbing is most of its body. |
| Backdrop machinery | `comfy.py:59-76` (`BACKDROP`, `backdrop_prompt`), `background:` config (`styles/base_pixel.yaml:55-63`, consumed `stages/palette.py:63-68`) | Must be *disabled* for terrain (tiles have no backdrop; `background.enabled: false` already exists and is honoured at palette.py:65). Still wanted for *object* tiles (a pot, a signpost) that need transparency — so it becomes a per-config choice, mechanism unchanged. |
| `NEGATIVE` | `comfy.py:36-40` | Generic anti-blur/anti-photo terms transfer; "extra limbs, deformed" are harmless but noise. Worth a `TILE_NEGATIVE` adding the tile failure modes: perspective, horizon, vanishing point, isometric (when top-down is wanted), border/frame artifacts. `POSE_NEGATIVE` (comfy.py:80-84) is appended only when a pose control image is in play, so it self-excludes. |
| Union ControlNet | `apply_controlnet(union_type=…)` (comfy.py:309-346) | The promax Union model handles ten types including **tile** and **canny/lineart**. For structured tiles (cliff edges, path corners) a drawn edge-mask control with `union_type: "canny"` reuses the entire splice unchanged — only the control *image producer* is new (§4). Nothing pose-specific in the function. |
| `tools/pixelize.py` CLI | 357-byte shim over `pipeline/pixelize.py` | Works today on any PNG, including a hand-drawn tile — the zero-code path to test 64×64 factor/palette choices before any stage exists. |
| Training/dataset tooling | `pipeline/training.py` — `estimate_block_size` (:198), `inspect`/`assess`/`plan` (:246,319,392) | Feature-scale measurement is subject-agnostic and applies to tile training images directly. `figure_height` (:375) is character-specific — a tile "fills the frame" by definition — so `plan()`'s normalisation-by-figure-height needs a tileset branch or bypass. |

## 3. Character-specific — irrelevant to tiles, do not touch

`pipeline/rigs.py` (all of it), `pipeline/bodyspace.py`, `pipeline/openpose.py`,
`pipeline/depthmap.py` (renders depth *from a skeleton*), `pipeline/autorig.py`,
`pipeline/annotate.py` (proportion measurement), `pipeline/detect.py` (rig
detection), `pipeline/softbody.py` + `stages/softbody.py` (secondary motion),
`pipeline/props.py` (weapons attach to joints), `stages/pose.py`,
`stages/depth.py`, `stages/frames.py` (its body is skeleton/depth-control
orchestration; the reusable parts are already factored into `comfy.py`),
`pipeline/llm.py`'s pose generation (`generate_pose`, `load_example`) — though
the `Ollama` client class itself (llm.py) is generic and already reused by the
palette chooser. `poses/`, `props/weapons.yaml`, `inputs/*.png` likewise.

## 4. Genuinely new work

1. **`TileStage` (GPU)** — the variant generator. Prompt from
   subject+style-sheet vocabulary via `styles.layer`, anchor tile via
   `apply_ipadapter`, optional edge-mask via `apply_controlnet(union_type=
   "canny")`, `sample_and_save(batch=…)`. Mostly assembly of existing calls;
   estimate ~150 lines modelled on `stages/canonical.py` minus pose.
2. **Seamlessness.** Nothing in the tree addresses wrap-around edges. Options,
   in increasing cost: (a) generate 1024, pixelize, then *measure* the wrap
   seam (reuse the reconstruction-error idea from
   `training.estimate_block_size`, applied across the toroidal boundary) and
   reject/retry — fits the existing generate-then-check pattern
   (`palettes.py` docstring, pose validation); (b) offset-and-inpaint pass
   through a second ComfyUI graph; (c) circular-padding patches — needs custom
   nodes, avoid. Start with (a): it is a checker, not a generator, and the
   queue's retry machinery already exists.
3. **Autotile/adjacency layout.** Wang/blob tile sets need the export stage to
   place variants in engine-expected order and record adjacency in
   `sheet.json`. New data (a layout table per tileset kind), small code.
4. **Edge-mask authoring.** For terrain transitions, a control-image producer:
   47-blob or 16-Wang masks are procedurally trivial (numpy). New ~100 lines;
   replaces the pose stage as the "control image producer" slot in the stage
   graph — same artifact shape (`produces = {"control_images"}`), which keeps
   the runner's dependency validation meaningful.
5. **`module: tileset` registration** — schema fields (`tile.size`,
   `tile.terrain`, `tile.variants`, `tile.seamless`, layout kind), style-sheet
   `modules.tileset` templates, and a minimal web Input surface (the current
   Input tab is rig/reference-shaped). Config examples under `configs/`.
6. **A tileset style sheet** — data only: extend `base_pixel`, top-down
   vocabulary, pinned environment palette. `pokemon_mono` is the template.

## 5. Decisions already paid for that tiles inherit free

Read `DECISIONS.md` before implementing; these transfer directly:
`--gpu-only` (don't), LCM ignores control below ~12 steps, Union ControlNet
must be told its type, named backdrop beats described (for object tiles),
median-cut value-collapse → clustered metrics, curves before quantisation,
palette size IS detail (12 was too few for armour; terrain will want 16–32),
match metrics (`luma` for sprites — verify against terrain, where hue may
matter more than value), one-model-not-several (a tileset LoRA is the same
"style vs view coverage" split), queue broken-vs-not-ready semantics.
