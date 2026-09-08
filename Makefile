# cubic-sdk

VENV ?= .venv
PY   ?= python3

.PHONY: help install test live build check clean

help:
	@echo "make install  install the SDK and its dev dependencies (editable)"
	@echo "make test     the mock suite — no network, no key"
	@echo "make live     the live suite against CUBIC_BASE_URL with CUBIC_API_KEY"
	@echo "make build    build the sdist and wheel, and twine-check them"

install:
	$(PY) -m pip install -e ".[dev]"

# The default suite. Every request is answered by an httpx.MockTransport, so
# this needs no service, no key and no network.
test:
	$(PY) -m pytest -q

# The live suite: the SDK against a running Cubic.
#
#     CUBIC_BASE_URL=http://127.0.0.1:8010 CUBIC_API_KEY=mxk_… make live
#
# Both variables are required. Neither has a default and neither is stored
# here: a key belongs in your shell (or your CI's secrets), and naming the
# deployment is what stops a key exported for something else from pointing this
# suite at the hosted API. Without them the live cases skip and the run is
# green on the mock suite alone.
live:
	$(PY) -m pytest -q -m live -rs

build:
	$(PY) -m pip install -q --upgrade build twine
	rm -rf dist
	$(PY) -m build
	$(PY) -m twine check dist/*

check: test build

clean:
	rm -rf dist build .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
