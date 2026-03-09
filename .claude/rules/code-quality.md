---
paths:
  - "**/*.py"
---

# Code Quality Checks

## When to run

Run ALL checks below in these scenarios:
- After modifying any Python code
- Before creating a git commit that includes Python file changes (even if changes were made in a previous session or externally)

## Checks

1. `uv run ruff check src/ tests/` — lint (style, imports, simplification)
2. `uv run ruff format --check src/ tests/` — format (must match CI)
3. `uv run pyright src/ tests/` — type checking (type errors, missing annotations)

If format check fails, run `uv run ruff format src/ tests/` to auto-fix.

Fix all reported errors before considering the task complete. Zero errors is the baseline.
