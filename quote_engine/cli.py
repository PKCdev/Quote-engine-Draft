from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

# Lazy imports inside functions for optional deps: yaml, pandas, openpyxl, jinja2

app = typer.Typer(help="PKC Quote Engine CLI")


def _find_file(project_dir: Path, keyword: str, exts=(".xlsx", ".xlsm", ".csv")) -> Optional[Path]:
    for p in project_dir.iterdir():
        if p.suffix.lower() in exts and keyword.lower() in p.name.lower():
            return p
    return None


@app.command()
def price(
    project_dir: str = typer.Argument(..., help="Project folder with MV exports"),
    configs: str = typer.Option("configs", help="Config/catalogs folder"),
    out: Optional[str] = typer.Option(None, help="Output folder for results"),
):
    """Parse MV reports, compute costs, and emit internal + client outputs.

    Inputs (expected inside project_dir):
    - Work Order Summary (xlsx)
    - Processing Station Parts (csv)
    - Product List (csv)
    - Optional Buyout Report (xlsx)
    """
    base = Path(project_dir)
    cfg_dir = Path(configs)
    out_dir = Path(out) if out else base / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Locate inputs
    wos = _find_file(base, "Work Order Summary")
    parts = _find_file(base, "Processing Station Parts")
    # Accept either Product List or Delivery Product Check List
    products = _find_file(base, "Product List") or _find_file(base, "Delivery Product Check List")
    buyout = _find_file(base, "Buyout")

    # Load configs
    try:
        import yaml  # type: ignore
    except Exception as e:
        typer.echo("PyYAML not installed. Install with: pip install pyyaml")
        raise typer.Exit(code=2)

    with open(cfg_dir / "rates.yaml", "r", encoding="utf-8") as f:
        rates = yaml.safe_load(f) or {}
    with open(cfg_dir / "policy.yaml", "r", encoding="utf-8") as f:
        policy = yaml.safe_load(f) or {}
    with open(cfg_dir / "materials.yaml", "r", encoding="utf-8") as f:
        materials_cat = yaml.safe_load(f) or {}
    # Optional attribute-based pricing
    mat_attr_pricing = {}
    if (cfg_dir / "materials_pricing.yaml").exists():
        with open(cfg_dir / "materials_pricing.yaml", "r", encoding="utf-8") as f:
            mat_attr_pricing = yaml.safe_load(f) or {}
    with open(cfg_dir / "edgeband.yaml", "r", encoding="utf-8") as f:
        bands_cat = yaml.safe_load(f) or {}
    with open(cfg_dir / "hardware.yaml", "r", encoding="utf-8") as f:
        hardware_cat = yaml.safe_load(f) or {}
    hw_aliases = {}
    if (cfg_dir / "hardware_aliases.yaml").exists():
        with open(cfg_dir / "hardware_aliases.yaml", "r", encoding="utf-8") as f:
            hw_aliases = yaml.safe_load(f) or {}
    with open(cfg_dir / "assembly_rules.yaml", "r", encoding="utf-8") as f:
        assembly_rules = yaml.safe_load(f) or {}

    # Importers
    from .importers import wos_xlsx, parts_csv, products_csv, buyout_xlsx

    wos_data = wos_xlsx.parse(wos) if wos else {"sheets": [], "edgeband": [], "hardware": []}
    parts_data = parts_csv.parse(parts) if parts else {"parts": []}
    products_data = products_csv.parse(products) if products else {"products": []}
    buyout_data = buyout_xlsx.parse(buyout) if buyout else {"buyouts": []}
    # Attach hardware to products for counts/scaling
    if isinstance(products_data, dict):
        products_data["hardware"] = wos_data.get("hardware", [])

    # Calculations (category-wise)
    from .calculators import materials, edgeband, hardware, cnc, assembly, install, overhead, pricing

    mat_bd = materials.breakdown(wos_data.get("sheets", []), materials_cat, policy, mat_attr_pricing)
    mat_cost = mat_bd["total"]
    eb_result = edgeband.compute(wos_data.get("edgeband", []), bands_cat, policy, rates)
    eb_cost = eb_result["cost"]
    hw_cost = hardware.compute(wos_data.get("hardware", []), hardware_cat, hw_aliases)

    cnc_result = cnc.estimate_from_materials(mat_bd, rates, (proj_overrides.get("materials", {}) if 'proj_overrides' in locals() else {}))
    # Load per-project overrides if present
    overrides_path = base / "overrides.json"
    proj_overrides = {}
    if overrides_path.exists():
        proj_overrides = json.loads(overrides_path.read_text(encoding="utf-8")) or {}
    product_overrides = proj_overrides.get("products", {})

    asm_result = assembly.estimate(products_data, assembly_rules, rates, product_overrides)
    inst_result = install.estimate(products_data, rates, assembly_rules, product_overrides)

    # Override edgeband time with per-edge model from parts report
    eb_hours_edges = edgeband.time_from_parts(parts_data, rates)
    eb_result["hours"] = eb_hours_edges

    totals = {
        "materials": mat_cost,
        "edgeband": eb_cost,
        "hardware": hw_cost,
        "cnc": cnc_result["cnc"]["cost"],
        "panel_saw": cnc_result["panel_saw"]["cost"],
        "assembly": asm_result["cost"],
        "install": inst_result["cost"],
    }

    overhead_cost = overhead.allocate(
        drafting_hours=rates.get("defaults", {}).get("drafting_hours", 10),
        pm_hours=rates.get("defaults", {}).get("pm_hours", 10),
        assembly_hours=asm_result.get("hours", 0.0),
        rates=rates,
    )
    totals["overhead"] = overhead_cost

    price_summary = pricing.price(totals, rates, policy)

    # Save internal breakdown JSON
    with open(out_dir / "internal_breakdown.json", "w", encoding="utf-8") as f:
        json.dump({
            "inputs": {
                "wos": wos.name if wos else None,
                "parts": parts.name if parts else None,
                "products": products.name if products else None,
                "buyout": buyout.name if buyout else None,
            },
            "wos": wos_data,
            "derived": {
                "parts": parts_data,
                "products": products_data,
                "buyout": buyout_data,
            },
            "materials_breakdown": mat_bd,
            "time": {
                "cnc": cnc_result["cnc"]["hours"],
                "panel_saw": cnc_result["panel_saw"]["hours"],
                "edgeband": eb_result["hours"],
                "assembly": asm_result["hours"],
                "install": inst_result["hours"],
            },
            "totals": totals,
            "price": price_summary,
        }, f, indent=2)

    # Render client HTML (Cambridge-style)
    try:
        from .output.exporters.html import render_client_quote
        # meta.json is optional; merge company.yaml if present
        meta = {"name": base.name}
        meta_path = base / "meta.json"
        if meta_path.exists():
            try:
                meta.update(json.loads(meta_path.read_text(encoding="utf-8")) or {})
            except Exception:
                pass
        comp_path = cfg_dir / "company.yaml"
        if comp_path.exists():
            try:
                import yaml as _yaml

                meta.setdefault("company", (_yaml.safe_load(comp_path.read_text(encoding="utf-8")) or {}))
            except Exception:
                pass
        html = render_client_quote(
            project_name=base.name,
            materials=wos_data.get("sheets", []),
            edgeband=wos_data.get("edgeband", []),
            hardware=wos_data.get("hardware", []),
            assembly=asm_result,
            install=inst_result,
            price=price_summary,
            rates=rates,
            policy=policy,
            category_totals=totals,
            materials_breakdown=mat_bd,
            products=products_data,
            buyout=buyout_data,
            hardware_catalog=hardware_cat,
            hardware_aliases=hw_aliases,
            meta=meta,
        )
        (out_dir / "quote.html").write_text(html, encoding="utf-8")
    except Exception as e:
        typer.echo(f"[warn] could not render client HTML: {e}")

    typer.echo(f"Done. Outputs in: {out_dir}")


@app.command()
def extract_catalogs(xlsm_path: str, out: str = typer.Option("configs", help="Output folder")):
    """Extract vendor price tables from an XLSM into YAML catalogs (best-effort)."""
    try:
        from .importers import catalogs_xlsm  # type: ignore
    except Exception as e:
        typer.echo("Catalog extractor not implemented yet.")
        raise typer.Exit(code=1)
    # Placeholder
    typer.echo("Catalog extraction is a future milestone.")


@app.command()
def tune(project_dir: str, actuals: Optional[str] = None):
    """Update coefficients using post-job actuals (EMA).

    Placeholder: records the existence of actuals; tuning logic will be added in M2.
    """
    typer.echo("Tuning placeholder. Will update cnc and assembly coefficients in future milestones.")


@app.command()
def validate(project_dir: str, configs: str = "configs"):
    """Validate presence of expected files and mappings; report gaps."""
    base = Path(project_dir)
    wos_f = _find_file(base, "Work Order Summary")
    parts_f = _find_file(base, "Processing Station Parts")
    products_f = _find_file(base, "Product List") or _find_file(base, "Delivery Product Check List")
    missing = []
    if not wos_f:
        missing.append("WOS")
    if not parts_f:
        missing.append("Parts CSV")
    if not products_f:
        missing.append("Products CSV (Product List or Delivery Product Check List)")
    if missing:
        typer.echo(f"Missing inputs: {', '.join(missing)}")
        raise typer.Exit(code=2)
    typer.echo("OK: Required inputs present.")


if __name__ == "__main__":  # pragma: no cover
    app()
