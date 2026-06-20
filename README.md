# Saltcode — Pi-Optimized AI-Driven Coding Framework

Saltcode is a terminal-driven framework that automates software development using a two-phase agentic process (Phase 1: Scout, Architect, Planner, Test Intent, Evaluator; Phase 2: Builder, Static Gate, Test Runner, Auditor).

## Project Structure

- `docs/` — Core proposals and developer guides.
- `specs/` — Requirements, design, and task list.
- `saltcode/` — Core source code of the framework.
- `tests/` — Framework tests.
- `workspace/` — Environment projects being modified by Saltcode.

## Installation

Ensure you have Python 3.11+ installed. Install the package with dev tools:

```bash
pip install -e ".[dev]"
```

## Running

Run the Typer-based CLI:

```bash
pip install -e .[dev]
pip install -e .[cli]
python -m saltcode.cli --help
```
