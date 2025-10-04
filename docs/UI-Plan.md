# UI Plan — PKC Quote Engine

## Goals
- Simple, local web UI to import MV reports, review parsed data, adjust parameters, and generate client quotes.
- Hide internal coefficients; show clear totals and assumptions.

## Tech Stack
- Backend: FastAPI + Jinja2 templates (server-side HTML)
- Frontend: HTMX (progressive enhancement), Tailwind CSS (utility styles)
- Storage: SQLite (SQLModel) for jobs, catalogs versioning, and tuning logs
- Launch: `python -m quote_engine.web` (local only)

## Key Screens
- Dashboard: recent projects, New Quote button
- New Quote Wizard: upload MV files (WOS.xlsx, Parts.csv, Products.csv, optional Buyout.xlsx); validate & parse
- Project Review: tabs for Materials, Edgeband, Hardware, Labor (assembly/install), Summary
  - Inline editors: waste (%), install hours, drafting/PM hours, complexity flags
- Catalogs: materials, edgeband, hardware (inline price edits, pack sizes)
- Settings: rates, policy (margin, rounding, overhead)
- Quote Preview: client HTML; Export as PDF/HTML; include scope and terms

## Core Flows
- Create Project → Upload files → Parse & normalize → Review & tweak → Generate Quote → Save/export
- Post-job: Enter actuals → Tune coefficients (CNC/assembly)

## Routes (initial)
- GET `/` Dashboard
- GET/POST `/projects/new` Upload wizard
- GET `/projects/{id}` Review (tabs)
- POST `/projects/{id}/recalc` Recompute with current settings
- GET `/projects/{id}/quote` Preview
- POST `/projects/{id}/export` Save HTML/PDF
- GET/POST `/catalogs/*` Price maintenance
- GET/POST `/settings` Rates/Policy

## Data Model (UI layer)
- Project(id, name, created_at, status)
- Files(wos, parts, products, buyout)
- Parsed(wos sheets/eb/hardware, derived parts/products)
- Adjustments(hours, waste, overrides)
- Outputs(internal JSON, client HTML path)

## MVP Acceptance
- Uploads validate and parse successfully for current samples
- Editable hours (drafting, PM, assembly, install) and extra sheet waste
- Quote preview renders and exports HTML; totals match CLI
- No external network; data stored locally

## Phase 2
- PDF export, catalogs editing UI, tuning dashboard, per-product assembly breakdown

