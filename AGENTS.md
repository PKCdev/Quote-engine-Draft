# Repository Guidelines

## Project Structure & Module Organization
- `MV_reports/` — sample Microvellum (MV) reports used for parsing and calibration.
- `plan.txt` — vision and scope notes.
- Future layout (target):
  - `quote_engine/` (Python package: importers, calculators, output templates)
  - `configs/` (YAML catalogs: materials, edgeband, hardware, rates, policy)
  - `tests/` (pytest unit tests)
  - `projects/<name>/` (per‑job MV inputs and outputs)

## Build, Test, and Development Commands
- Create env and tools:
  - `python3 -m venv .venv && source .venv/bin/activate`
  - `pip install -U pip && pip install pandas openpyxl xlrd pyyaml pydantic typer jinja2 pytest`
- Run CLI (once implemented):
  - `python -m quote_engine price ./projects/<name>`
- Tests:
  - `pytest -q` (add `-k <pattern>` to filter)

## Coding Style & Naming Conventions
- Language: Python 3.12. Indentation: 4 spaces. Max line length ~88.
- Names: `snake_case` for modules/functions; `PascalCase` for classes; `SCREAMING_SNAKE_CASE` for constants.
- YAML files use `snake_case` keys and kebab-case filenames (e.g., `materials.yaml`).
- Prefer pure functions, small modules, and explicit types (pydantic models for configs).

## Testing Guidelines
- Framework: `pytest`.
- Test files: `tests/test_<module>.py`; name tests `test_<behavior>()`.
- Aim for coverage of importers, calculators, and pricing logic; add sample MV fixtures in `tests/fixtures/`.

## Commit & Pull Request Guidelines
- Commits: concise, imperative subject (optionally Conventional Commits), e.g., `feat(import): parse WOS edgeband totals`.
- PRs must include: purpose, scope of change, test notes, and before/after behavior. Link related issues.
- Keep patches focused; avoid unrelated refactors.

## Security & Configuration Tips
- Treat MV files as sensitive; redact client data in examples.
- Do not commit secrets. Use `.env.local` for local overrides and document required vars.
- Catalogs (prices, rates) are versioned; update with clear changelog notes.

## Agent-Specific Instructions
- Prefer incremental changes; follow this guideline’s structure and naming.
- When parsing MV reports, anchor on labeled sections (e.g., “Sheet Stock Totals”, “Edgeband Totals”, “Hardware Totals”).
- Keep outputs client‑friendly: expose totals and assumptions, not internal coefficients.
