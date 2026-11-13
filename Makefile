.PHONY: install test lint demo demo-site media clean

install:
	uv venv -q .venv && VIRTUAL_ENV=$$PWD/.venv uv pip install -q -e '.[dev]'

test:
	.venv/bin/python -m pytest --cov=agent_workbench --cov-report=term-missing

lint:
	.venv/bin/ruff check src tests scripts bench && .venv/bin/ruff format --check src tests

demo:
	.venv/bin/awb demo awb-demo --serve

# a static copy of the demo page (ticks stay in the browser): for GitHub Pages
demo-site:
	rm -rf .demo-build && .venv/bin/awb demo .demo-build >/dev/null
	rm -rf docs/demo && mkdir -p docs/demo && cp -r .demo-build/site/ocr-fixes docs/demo/ocr-fixes && cp .demo-build/site/ocr-fixes.html docs/demo/index.html
	rm -rf .demo-build

media:
	python scripts/make_media.py /tmp/awb-media

clean:
	rm -rf .demo-build dist build .coverage htmlcov
