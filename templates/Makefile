# agents-orchestrator — drop this Makefile at the root of any target repo.
#
# `make` with no target runs a SUPERVISED BURST: it works through the queue
# now, while you watch `make status` and step into any agent with
# `claude --resume <session-id>`.
#
# The unattended hourly mode (launchd) is OPT-IN — `make ao-on` enables it,
# `make ao-off` stops it.
#
# Tool source lives at AO_HOME (default ~/Desktop/Claudey/). Per-project
# config lives next to this Makefile in .agents-orchestrator.toml.

SHELL      := /bin/bash
AO_HOME    ?= $(HOME)/Desktop/Claudey
AO         := python3 $(AO_HOME)/agents_orchestrator/orchestrator.py
PLIST      ?= com.agents-orchestrator.$(shell basename "$$PWD").plist
PLIST_DEST := $(HOME)/Library/LaunchAgents/$(PLIST)

.DEFAULT_GOAL := burst
.PHONY: burst dry-burst slow dry-slow status web finish ao-install ao-on ao-off ao-status help

burst: ## Process the whole queue now, supervised (default; merges when green)
	$(AO) burst

dry-burst: ## Like burst, but never merges — use during a trial
	AO_DRY_RUN=1 $(AO) burst

slow: ## Process one issue/batch now, then exit
	$(AO) slow

dry-slow: ## Like slow, but never merges
	AO_DRY_RUN=1 $(AO) slow

status: ## Print the cockpit: queue, running agent, recent runs + session ids
	$(AO) status

web: ## Start the local web cockpit (http://127.0.0.1:7777)
	$(AO) serve

finish: ## Merge PRs left open by an earlier stop_after run
	$(AO) finish

ao-install: ## Copy the launchd plist into ~/Library/LaunchAgents
	@test -f $(PLIST) || { echo "no $(PLIST) in $$PWD — copy templates/launchd.plist.example and edit"; exit 1; }
	cp $(PLIST) $(PLIST_DEST)
	@echo "installed $(PLIST_DEST)"

ao-on: ao-install ## Enable the hourly unattended loop (slow mode)
	launchctl load $(PLIST_DEST)
	@echo "agents-orchestrator scheduled — runs hourly in slow mode. Stop it with: make ao-off"

ao-off: ## Disable the hourly loop (in-flight runs are unaffected)
	-launchctl unload $(PLIST_DEST) 2>/dev/null
	@echo "scheduled mode disabled — no further unattended runs"

ao-status: ## Show whether the launchd job is currently loaded
	@launchctl list | grep $(basename $(PLIST)) || echo "not loaded (disabled)"

help: ## List every target
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  make %-12s %s\n", $$1, $$2}'
