# Sprite pipeline.
#
#   make up      start ComfyUI + Ollama + web UI, wait until healthy
#   make down    stop everything make started
#   make run     run a pipeline
#
# Service logic lives in scripts/ctl.sh, not here: macOS ships GNU Make 3.81,
# which predates .ONESHELL, so every recipe line would be its own shell and any
# loop would have to be crammed onto one backslash-continued line.

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PY   := $(ROOT)/ComfyUI/.venv/bin/python
RUFF := $(ROOT)/ComfyUI/.venv/bin/ruff
CTL  := $(ROOT)/scripts/ctl.sh

# Which config `make run` uses.
CONFIG ?= knight_attack

# Passed to ComfyUI. Empty on purpose — see the note in scripts/ctl.sh.
VRAM_MODE ?=
export VRAM_MODE

.PHONY: help up down restart status logs run check test poses clean queue autopilot

help:
	@printf '\n\033[1mSprite pipeline\033[0m\n\n'
	@printf '  \033[1mmake up\033[0m        start everything, wait until healthy\n'
	@printf '  \033[1mmake down\033[0m      stop everything make started\n'
	@printf '  \033[1mmake restart\033[0m   down, then up\n'
	@printf '  \033[1mmake status\033[0m    service health + latest run\n'
	@printf '  \033[1mmake logs\033[0m      follow all service logs\n\n'
	@printf '  \033[1mmake run\033[0m       run a pipeline   \033[2mmake run CONFIG=knight_attack\033[0m\n'
	@printf '  \033[1mmake check\033[0m     validate every config, run nothing\n'
	@printf '  \033[1mmake test\033[0m      run the frontend + api test suites\n'
	@printf '  \033[1mmake poses\033[0m     rebuild pose library + previews\n\n'
	@printf '  \033[1mmake queue\033[0m     show the job queue\n'
	@printf '  \033[1mmake autopilot\033[0m run the queue unattended\n'
	@printf '  \033[1mmake clean\033[0m     remove pids, logs, caches (keeps out/)\n\n'
	@printf '  \033[2mconfigs: %s\033[0m\n\n' "$$(ls configs/*.yaml 2>/dev/null | xargs -n1 basename | sed 's/\.yaml//' | tr '\n' ' ')"

up:      ; @$(CTL) up
down:    ; @$(CTL) down
restart: ; @$(CTL) restart
status:  ; @$(CTL) status
logs:    ; @$(CTL) logs

run:
	@test -f configs/$(CONFIG).yaml \
	  || { printf '\033[31mno configs/%s.yaml\033[0m\navailable: %s\n' "$(CONFIG)" \
	       "$$(ls configs/*.yaml | xargs -n1 basename | sed 's/\.yaml//' | tr '\n' ' ')"; exit 1; }
	@curl -sf -m 2 http://127.0.0.1:8188/system_stats >/dev/null 2>&1 \
	  || { printf '\033[31mComfyUI is not up.\033[0m Run: make up\n'; exit 1; }
	@$(PY) run.py configs/$(CONFIG).yaml

# _global.yaml holds machine-level defaults, not a pipeline — it has no stages
# to validate, so it is skipped rather than reported as broken.
check: lint
	@rc=0; for c in configs/*.yaml; do \
	  case "$$c" in */_global.yaml) continue;; esac; \
	  printf '  %-32s ' "$$c"; \
	  if $(PY) run.py "$$c" --explain >/dev/null 2>&1; \
	    then printf '\033[32mok\033[0m\n'; \
	    else printf '\033[31minvalid\033[0m\n'; rc=1; fi; \
	done; exit $$rc

# Undefined names are the one defect class that compiles cleanly, survives
# review, and then crashes six GPU-minutes into a run. `apply_ipadapter` shipped
# reading a name that was never a parameter; py_compile was happy, --explain was
# happy, and only calling it would have told us. pyflakes reads every branch
# without executing any of them, which is exactly the coverage a pipeline whose
# error paths cost real time needs.
#
# Only the rules that catch crashes are enabled. Unused imports are excluded
# deliberately: several of them are load-bearing. `from . import stages` looks
# unused and is the line that populates the stage registry — deleting it made
# every queued job fail validation once already.
lint:
	@printf '\033[1mstatic\033[0m\n'
	@$(RUFF) check --quiet --select F821,F811,F502,F506,F601,F632,B018 \
	  pipeline/ tools/ tests/ *.py \
	  || { printf '\033[31mstatic analysis failed\033[0m\n'; exit 1; }
	@for f in web/js/*.js; do node --check "$$f" || exit 1; done
	@printf '  \033[32mno undefined names\033[0m\n'

test:
	@printf '\033[1mfrontend\033[0m\n'; node tests/test_frontend.mjs
	@printf '\033[1mbackend + api\033[0m\n'; $(PY) tests/test_api.py

queue:
	@$(PY) autopilot.py --status

autopilot:
	@$(PY) autopilot.py

poses:
	@$(PY) tools/make_poses.py --preview --views all

clean:
	@rm -rf $(ROOT)/.run $(ROOT)/logs $(ROOT)/tools/__pycache__ \
	        $(ROOT)/pipeline/__pycache__ $(ROOT)/pipeline/stages/__pycache__
	@printf '  removed pids, logs and caches (out/ kept)\n'
