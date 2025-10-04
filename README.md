# PKC Quote Engine (MVP)

A local tool that converts Microvellum (MV) reports into accurate quotes with configurable catalogs and policies.

## Quickstart (no coding required)
1) Create the environment and install tools
- `./scripts/bootstrap.sh`

2) Price the sample reports already in this repo
- `./scripts/price_sample.sh`
- Outputs: `MV_reports/out/quote.html` and `MV_reports/out/internal_breakdown.json`

3) Validate inputs
- `source .venv/bin/activate`
- `python -m quote_engine.cli validate MV_reports`

## Project inputs
Place your MV exports for a job in a folder (or reuse `MV_reports/`):
- Work Order Summary `.xlsx`
- Processing Station Parts `.csv`
- Product List `.csv`
- Optional Buyout report `.xlsx`

## Configs you can edit
- `configs/rates.yaml` — labor/machine rates, installer billing, overhead, defaults
- `configs/policy.yaml` — target margin, rounding, waste
- `configs/materials.yaml` — price per m² (AUD ex-GST) with optional default sheet size
- `configs/edgeband.yaml` — band prices per meter and setup
- `configs/hardware.yaml` — hardware prices and pack sizes
- `configs/assembly_rules.yaml` — area factors and adders by product type

## CLI reference
- `python -m quote_engine.cli price <project_dir> --configs configs`
- `python -m quote_engine.cli validate <project_dir>`

See `docs/PRD.md` and `AGENTS.md` for architecture and contributor guidance.

### Maintain catalogs from the UI
- Materials: http://127.0.0.1:8000/catalogs/materials (add by WOS name, price per m²)
- Hardware: http://127.0.0.1:8000/catalogs/hardware (add by WOS description, unit price, pack)

## Web UI (optional, simple)
- Start UI: `. .venv/bin/activate && python -m quote_engine.web`
- Open http://127.0.0.1:8000
- Create a new quote, upload MV files, tweak hours/waste, and view the client quote.
