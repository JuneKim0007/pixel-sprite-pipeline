#!/usr/bin/env bash
# Start ComfyUI with the flags that matter on Apple Silicon.
#
#   PYTORCH_ENABLE_MPS_FALLBACK  routes the handful of ops Metal lacks to the
#                                CPU instead of crashing the run.
#   --use-pytorch-cross-attention  measurably faster than the default path on M3/M4.
#
# NOTE: --gpu-only is deliberately NOT used. It is worth ~8% for plain SDXL,
# but the pipeline also loads ControlNet (2.4GB), IP-Adapter and a CLIP vision
# encoder (2.4GB). That working set exceeds 16GB, and --gpu-only forbids
# ComfyUI from offloading, so macOS swaps to disk instead. Measured: one frame
# took 113s without the flag and was on track for ~9 minutes with it, with
# 11.6GB of swap in use. Let ComfyUI manage residency.
#
# Set VRAM_MODE to override, e.g. VRAM_MODE=--lowvram ./start.sh
#
# Web UI: http://127.0.0.1:8188
set -euo pipefail
cd "$(dirname "$0")/ComfyUI"

exec env PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python main.py \
  --use-pytorch-cross-attention \
  ${VRAM_MODE:-} \
  --listen 127.0.0.1 \
  --port 8188 \
  "$@"
