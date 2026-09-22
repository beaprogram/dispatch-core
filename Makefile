.PHONY: help install lint test bench fetch clean

help:
	@echo "Dispatch Core - road graph dispatch system"
	@echo ""
	@echo "Targets:"
	@echo "  install     Install package and dev dependencies"
	@echo "  lint        Run ruff check and mypy"
	@echo "  test        Run pytest with coverage"
	@echo "  bench       Run all benchmarks"
	@echo "  fetch       Download graph data (requires internet)"
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
	python -m pytest benchmarks/ -v

fetch:
	python scripts/fetch_graph.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage
