PYTHON ?= python
UP_ARGS ?=

.PHONY: up
up:
	$(PYTHON) scripts/up.py $(UP_ARGS)
