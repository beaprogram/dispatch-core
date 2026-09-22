# Dispatch Core

Road graph dispatch system with quadtree routing and A* pathfinding.

**Status:** Under development (checkpoints 0-6 planned).

## Quick Start

```bash
make install
make test
make bench      # requires data/cache/full_graph.graphml
```

## Architecture

*Detailed architecture diagram and design decisions coming in CP6.*

## Project Structure

- `src/dispatch_core/` - main package
- `tests/` - test suite
- `benchmarks/` - benchmark scripts and results
- `data/fixtures/` - small committed fixture for CI
- `data/cache/` - gitignored full graph

## License

MIT License. Map data from OpenStreetMap contributors (ODbL).
