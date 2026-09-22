PYTHON ?= python
UP_ARGS ?=

.PHONY: up test
up:
	$(PYTHON) scripts/up.py $(UP_ARGS)

test:
	$(PYTHON) -m pytest -q
