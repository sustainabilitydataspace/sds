PYTHON ?= python3

.PHONY: help public-env up down smoke gate-e4-r8 gate-e4-r10 test-public verify-public deliverables-check subsidy-closure-check

help:
	@printf '%s\n' 'SDS public release commands' \
	  '  make public-env          Generate local ignored secrets' \
	  '  make up && make smoke    Start and qualify the public demonstration profile' \
	  '  make gate-e4-r8          Run 1,000 synthetic raw transformations' \
	  '  make gate-e4-r10         Run 1,000 synthetic strict imports under 30 seconds' \
	  '  make verify-public       Run the source and runtime public verification lane' \
	  '  make deliverables-check  Validate public deliverable register and checksums'

public-env:
	$(MAKE) -C api public-env

up:
	$(MAKE) -C api up

down:
	$(MAKE) -C api down

smoke:
	$(MAKE) -C api smoke

gate-e4-r8:
	$(MAKE) -C api gate-e4-r8

gate-e4-r10:
	$(MAKE) -C api gate-e4-r10

test-public:
	$(MAKE) -C api test-public

verify-public:
	$(MAKE) -C api verify-public

deliverables-check:
	$(PYTHON) scripts/check_deliverables.py

subsidy-closure-check:
	$(PYTHON) scripts/subsidy_closure_check.py
