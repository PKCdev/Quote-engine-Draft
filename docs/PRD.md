# Product Requirements Document (PRD)

## 1) Overview
Perth Kitchen Crafters (PKC) needs a Microvellum-driven quoting engine that converts MV reports into accurate, repeatable, and explainable quotes. The system trusts MV Work Order Summary for counts (sheets, edgeband, hardware), augments with derived metrics (parts count, perimeter), applies configurable rates and policies, and outputs a client-ready quote plus an internal breakdown. Accuracy must improve over time via post-job calibration.

Goals
- Produce clear, professional client quotes (contract-like scope) with totals and assumptions.
- Be accurate, auditable, and fast to iterate. Target GM ≥ 30%.
- Be tuneable after each job (materials, EB, CNC, assembly, install).
- Keep internals configurable in YAML (catalogs, rates, rules). No hardcoding.

Out of Scope (MVP)
- Panel optimization/nesting, supplier API price sync, multi-user cloud, inventory/ERP.

Success Metrics
- Materials and EB cost within ±10%; labor within ±15% by job #3–5.
- Quote prep time < 30 minutes per project after setup.

## 2) Users & Core Flows
Personas
- Owner/Estimator: imports MV outputs, reviews costs, adjusts hours, exports client quote.
- Installer/Subcontractor (indirect): provides actuals for calibration.

Primary Flow
1. Export MV reports (WOS.xlsx, Parts.csv, Products.csv, optional Buyout/Hardware).
2. Run CLI: import → normalize → calculate → output.
3. Review internal breakdown; tweak overrides (hours, waste, rates) if needed.
4. Generate client quote (HTML/PDF/Excel). Send and track.
5. After job: record actuals; run tuning to update coefficients/catalogs.

## 3) Inputs & Contracts
Required Inputs (project folder)
- Work Order Summary (xlsx):
  - Sheet Stock Totals (Optimized): material names, sheet sizes, “Qty - N”.
  - Edgeband Totals: band specs, “Lin. Meters - …”.
  - Hardware Totals: “Qty - N … Item Name”.
- Processing Station Parts (csv): Room, Item, Product, Part, Qty, Width, Length, Material, EB edge flags, machine flags.
- Product List (csv): Item, Room, Description, Qty, Width/Height/Depth, Spec Group.

Optional Inputs
- Buyout Report (xlsx): QTY, DESCRIPTION, LENGTH, WIDTH, HEIGHT, MATERIAL.
- StirlingDesign Quoting Template (xlsm): vendor/unit price tables (bootstrap catalogs).

Assumptions
- Files follow MV’s consistent structure and labels; parsing anchors on headings (e.g., “Sheet Stock Totals”, “Edgeband Totals”, “Hardware Totals”).

## 4) Functional Requirements
4.1 Import & Normalize
- WOS importer (xlsx): extract sheet counts by material and sheet size; EB meters by band spec; hardware items and quantities.
- Parts importer (csv): compute total parts, edged parts, total routing perimeter (sum 2×(L+W) where applicable), by room/product.
- Product List importer (csv): per-product dimensions, type, room.
- Buyout importer (xlsx): map to sq.m where relevant; mark TBQ/excluded where missing rates.
- Normalization: map raw names to canonical materials/bands/SKUs via YAML maps; convert units; enforce types.

4.2 Catalogs & Config
- YAML catalogs: materials.yaml, edgeband.yaml, hardware.yaml (prices ex-GST, sizes, pack rules), assembly_rules.yaml (area factors, adders, complexity), material/band/hardware name maps.
- Rates/policy: AUD, GST 10%, GM target 30%+, shop=150/h, drafting=200/h, PM=125/h, installer billed=95/h; subcontractor internal cost=75/h (for internal margin views); overhead=$6,516/month with 120–160 internal hours; rounding=$10; default waste=8%; extra sheet waste factor configurable.

4.3 Calculators
- Materials (authoritative via WOS): cost = sheets × sheet_cost × (1 + extra_sheet_waste). Optionally cross-check with area-based estimate for diagnostics.
- Edgebanding (via WOS): for each band spec, cost = LM × price_per_m + setups × setup_cost; time = setup_time × setups + LM × time_per_m.
- Hardware (via WOS): cost = Σ(qty × unit_price), respecting pack sizes; unmapped → TBQ/manual.
- CNC time model: inputs = total sheets (WOS), total part count, total routing perimeter (m). time_h = a×sheets + b×parts + c×perimeter_m; cost = time_h × machine/shop rate (or subcontract price). Coefficients a/b/c are configurable and tuned from job logs.
- Assembly (per product):
  - base_hours = area_m2 × factor_by_type (area_m2 = H×W/1e6 from Product List).
  - adders = drawers×k_d + doors×k_do + adj_shelf×k_as + fixed_shelf×k_fs.
  - complexity multiplier per product where flagged.
  - cost = hours × shop rate. Export per-product hours for visibility.
- Install & delivery: total install hours × (0.8×2 people + 0.2×1 person) × role rates; allow PM/you to be part of the crew.
- Overhead: overhead_per_hour = 6516 ÷ internal_hours (120–160); apply to drafting/PM/assembly (install excluded by default).
- Pricing & taxes: subtotal_ex_gst = direct costs + overhead; price_ex_gst = subtotal_ex_gst ÷ (1 − GM_target); GST 10% added last; rounding to nearest $10.

4.4 Outputs
- Internal CSV/JSON: by room/product and category (materials, EB, hardware, CNC, assembly, install, overhead), including hours and key drivers (sheets, LM, parts, perimeter, area).
- Client Quote (HTML, optional PDF/Excel):
  - Sections: Sheet Materials (+ waste), Hardware, Buyouts (included/TBQ/excluded), Labor (drafting/assembly/install, crew description), Summary (ex-GST, GST, inc-GST), Assumptions, Inclusions, Exclusions, Allowances, Variations, Payment & Schedule, Acceptance.
  - Keep internals private (no coefficients, no overhead lines).

4.5 Tuning & Calibration
- After-job actuals (hours, sheets, LM) are stored per project.
- Tuning updates a/b/c (CNC) and assembly factors/adders via exponential moving average; catalogs grow with new SKUs.
- Keep versioned configs and a changelog of adjustments.

## 5) Non-Functional Requirements
- Offline-first (local venv, YAML, SQLite). Parse a project in < 10s (typical size).
- Reliable parsers anchored to WOS/CSV labels; clear error messages and missing-map diagnostics.
- Maintainable: small modules, typed configs, unit tests for parsers/calculators.
- Privacy: do not publish client PII; redact examples; keep price files local.

## 6) Architecture & Stack
- Python 3.12; pandas, openpyxl/xlrd for IO; PyYAML; Pydantic for config; Typer CLI; Jinja2 for HTML; pytest; optional SQLModel/SQLite for logs.
- Modules:
  - importers/: wos_xlsx.py, parts_csv.py, products_csv.py, buyout_xlsx.py, catalogs_xlsm.py
  - calculators/: materials.py, edgeband.py, hardware.py, cnc.py, assembly.py, install.py, overhead.py, pricing.py
  - output/: templates/, html.py, xlsx.py
  - store/: sqlite.py (actuals, tuning)
  - cli.py (commands)

Directory layout (target)
- quote_engine/, configs/, tests/, projects/<name>/, MV_reports/ (samples), docs/

## 7) Data Model (key entities)
- Project: id, name, client, currency, gst, gm_target, created_at.
- Room: name, order.
- Product: id, room, type, W×H×D, qty, complexity_factor.
- Part: product_id, material, L×W, qty, edged_flags, machine_flags.
- MaterialUsage: material_key, sheet_size, sheets_qty (WOS), extra_waste%.
- BandUsage: band_key, lm_total (WOS), setups.
- HardwareItem: key/SKU, desc, qty (WOS), unit_price.
- Buyout: item, qty/area, rate, status (included/TBQ/excluded).
- LaborEntry: category (drafting/pm/assembly/install), hours, people, role, rate.
- Rates/Policy: labor_rates, install_team rule, machine_rates, overhead, rounding, taxes.
- Catalogs: materials, bands, hardware price maps; normalization maps.
- Tuning: cnc_coeffs (a,b,c), assembly_factors and adders, history.

## 8) Pricing Details (formulas)
- materials_cost = Σ_m (sheets_wos[m] × sheet_cost[m] × (1 + extra_sheet_waste[m|global]))
- eb_cost = Σ_b (LM[b] × price_per_m[b]) + setups[b] × setup_cost[b]
- cnc_time_h = a×sheets_total + b×parts_total + c×perimeter_m_total; cnc_cost = cnc_time_h × rate
- assembly_hours(product) = area_m2 × factor[type] + drawers×k_d + doors×k_do + adj_shelf×k_as + fixed_shelf×k_fs; assembly_cost = hours × shop_rate
- install_cost = (0.8×2 + 0.2×1) × install_hours × billed_rate_by_role (crew roles configurable)
- overhead_per_hour = monthly_overhead ÷ internal_hours; overhead_cost = overhead_per_hour × (drafting + pm + assembly hours)
- price_ex_gst = (Σ costs) ÷ (1 − gm_target); total_inc_gst = price_ex_gst × 1.10; round to $10

## 9) CLI & UX
- `quote price <project_dir> [--configs ./configs] [--out ./out]` → parse inputs, compute, write internal.csv/json + client.html.
- `quote extract-catalogs <xlsm>` → seed YAML price catalogs from vendor sheets.
- `quote tune <project_dir> --actuals ./actuals.yaml` → update coefficients with post-job data.
- `quote validate <project_dir>` → check missing catalogs/maps and report gaps.

## 10) Error Handling & Diagnostics
- Missing mappings (material/band/SKU) → warnings with line references; default TBQ lines in outputs.
- Parser anchors not found → actionable error (which section missing).
- Show a brief “drivers” panel: sheets by material, EB LM by band, hardware count by category, key labor hours.

## 11) Testing Strategy
- Unit tests for each importer with sample fixtures (MV_reports/).
- Calculator tests with small deterministic inputs.
- Golden-file tests for client HTML (snapshot with relaxed whitespace).

## 12) Risks & Mitigations
- MV format drift → anchor on headings; keep mapping tables; add fuzzed tests.
- Price staleness → catalogs versioned; dated vendor updates; command to diff.
- Over/under-estimating labor → per-job tuning, conservative defaults, visible adjustments.

## 13) Roadmap & Milestones
M1 (MVP): WOS/CSV importers, catalogs, calculators, client HTML, internal CSV, basic config files.
M2: Hardware/Buyout enhancements, XLSM catalog extraction, per-product assembly breakdown, CLI tune.
M3: PDF/Excel export, SQLite actuals store, trend dashboards.
M4: Optional local web UI (FastAPI + Jinja2 + HTMX), role-based install scheduling.

## 14) Glossary
- WOS: Work Order Summary (MV Excel report).
- EB: Edgebanding (linear meters of band applied to part edges).
- GM: Gross Margin.
- TBQ: To Be Quoted (price missing/variable).

