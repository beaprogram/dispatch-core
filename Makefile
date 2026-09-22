.PHONY: help install lint test bench results fetch fetch-fixture clean

help:
	@echo "Dispatch Core - road graph dispatch system"
	@echo ""
	@echo "Targets:"
	@echo "  install     Install package and dev dependencies"
	@echo "  lint        Run ruff check and mypy"
	@echo "  test        Run pytest with coverage"
	@echo "  bench       Run all benchmarks and regenerate RESULTS.md"
	@echo "  results     Regenerate RESULTS.md from existing CSVs"
	@echo "  fetch       Download the 5000 m benchmark graph (requires internet)"
	@echo "  fetch-fixture  Download the 800 m test fixture (requires internet)"
	@echo "  clean       Remove build artifacts and cache"

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests benchmarks scripts
	ruff format --check src tests benchmarks scripts
	mypy src

test:
	pytest

bench:
	python -m benchmarks.bench_nearest
	python -m benchmarks.bench_routing
	python -m benchmarks.bench_dispatch
	python -m benchmarks.make_results

results:
	python -m benchmarks.make_results

fetch:
	python scripts/fetch_graph.py -o data/cache/halifax_5000m.graphml -r 5000

fetch-fixture:
	python scripts/fetch_graph.py -o data/fixtures/downtown_small.graphml -r 800

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage
