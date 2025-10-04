from __future__ import annotations

from pathlib import Path
from decimal import Decimal
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import (
    PolicyConfig,
    RatesConfig,
    QuoteData,
    MaterialLine,
    BuyoutLine,
    HardwareLine,
    LaborLine,
)
from .utils import to_decimal, money


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _calc_materials(lines: list[MaterialLine], waste_default: Decimal) -> tuple[list[dict], Decimal]:
    out: list[dict] = []
    total = Decimal(0)
    for line in lines:
        qty = to_decimal(line.qty)
        unit_cost = to_decimal(line.unit_cost)
        subtotal = to_decimal(line.subtotal) if line.subtotal is not None else qty * unit_cost
        waste_percent = to_decimal(line.waste_percent) if line.waste_percent is not None else waste_default
        waste_cost = to_decimal(line.waste_cost) if line.waste_cost is not None else subtotal * waste_percent / Decimal(100)
        total_cost = to_decimal(line.total_cost) if line.total_cost is not None else subtotal + waste_cost
        out.append(
            {
                "material": line.material,
                "thickness": line.thickness or "-",
                "sheet_size": line.sheet_size or "-",
                "qty": qty,
                "unit_cost": unit_cost,
                "subtotal": subtotal,
                "waste_cost": waste_cost,
                "total_cost": total_cost,
                "waste_percent": waste_percent,
            }
        )
        total += total_cost
    return out, total


def _calc_hardware(lines: list[HardwareLine]) -> tuple[list[dict], Decimal]:
    out: list[dict] = []
    total = Decimal(0)
    for line in lines:
        qty = to_decimal(line.qty)
        unit_cost = to_decimal(line.unit_cost)
        total_cost = to_decimal(line.total_cost) if line.total_cost is not None else qty * unit_cost
        out.append({"name": line.name, "qty": qty, "unit_cost": unit_cost, "total_cost": total_cost})
        total += total_cost
    return out, total


def _calc_buyout(lines: list[BuyoutLine]) -> tuple[list[dict], Decimal]:
    out: list[dict] = []
    included_total = Decimal(0)
    for line in lines:
        qty = to_decimal(line.qty)
        rate = to_decimal(line.rate_per_unit) if line.rate_per_unit is not None else Decimal(0)
        total_cost = to_decimal(line.total_cost) if line.total_cost is not None else qty * rate
        entry = {
            "material": line.material,
            "qty": qty,
            "unit": line.unit or "",
            "rate_per_unit": rate,
            "total_cost": total_cost,
            "status": line.status,
        }
        out.append(entry)
        if line.status == "included":
            included_total += total_cost
    return out, included_total


def _calc_labor(lines: list[LaborLine], rates: dict[str, Decimal]) -> tuple[list[dict], Decimal]:
    out: list[dict] = []
    total = Decimal(0)
    for line in lines:
        hours = to_decimal(line.hours)
        rate = to_decimal(line.rate) if line.rate else to_decimal(rates.get(line.category, Decimal(0)))
        total_cost = to_decimal(line.total_cost) if line.total_cost is not None else hours * rate
        out.append(
            {
                "category": line.category,
                "hours": hours,
                "rate": rate,
                "total_cost": total_cost,
                "description": line.description or "",
            }
        )
        total += total_cost
    return out, total


def render_client_quote(
    project_dir: Path,
    out_path: Path | None = None,
    configs_dir: Path | None = None,
) -> Path:
    # Load configs
    configs_dir = configs_dir or Path("configs")
    policy_cfg = PolicyConfig(**_load_yaml(configs_dir / "policy.yaml"))
    rates_cfg = RatesConfig(**_load_yaml(configs_dir / "rates.yaml"))

    # Load project overrides
    overrides_path = project_dir / "overrides.yaml"
    project_data = _load_yaml(overrides_path)
    quote = QuoteData(**project_data)

    # Calculations
    materials, materials_total = _calc_materials(quote.materials, policy_cfg.waste_percent_default)
    hardware, hardware_total = _calc_hardware(quote.hardware)
    buyout, buyout_included_total = _calc_buyout(quote.buyout)
    labor, labor_total = _calc_labor(quote.labor, {k: to_decimal(v) for k, v in rates_cfg.labor_rates.items()})

    # Adjustments
    design_fee = Decimal(0)
    if quote.adjustments.design_fee_percent:
        base = materials_total + hardware_total + buyout_included_total + labor_total
        design_fee = base * to_decimal(quote.adjustments.design_fee_percent) / Decimal(100)

    delivery = to_decimal(quote.adjustments.delivery) if quote.adjustments.delivery else Decimal(0)
    contingency = Decimal(0)
    if quote.adjustments.contingency_percent:
        sub = materials_total + hardware_total + buyout_included_total + labor_total + design_fee + delivery
        contingency = sub * to_decimal(quote.adjustments.contingency_percent) / Decimal(100)

    discount = Decimal(0)
    if quote.adjustments.discount_percent:
        sub = materials_total + hardware_total + buyout_included_total + labor_total + design_fee + delivery + contingency
        discount = sub * to_decimal(quote.adjustments.discount_percent) / Decimal(100)

    subtotal = materials_total + hardware_total + buyout_included_total + labor_total + design_fee + delivery + contingency - discount
    gst = subtotal * policy_cfg.gst_percent / Decimal(100)
    grand_total = subtotal + gst

    # Payment schedule amounts
    payments: list[dict[str, Any]] = []
    for m in policy_cfg.payment_schedule:
        base_amt = grand_total if m.base == "grand_total" else subtotal
        amt = base_amt * m.percent / Decimal(100)
        payments.append({"name": m.name, "percent": m.percent, "amount": amt, "description": m.description or ""})

    # Rooms (already structured in overrides)
    rooms = [
        {
            "code": r.code,
            "name": r.name,
            "count": len(r.products),
            "products": [{"index": p.index, "name": p.name, "dimensions": p.dimensions or ""} for p in r.products],
        }
        for r in quote.rooms
    ]

    # Jinja env
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    template = env.get_template("client_quote.html.j2")

    ctx = {
        "policy": policy_cfg,
        "quote": quote,
        "format_money": lambda x: money(to_decimal(x), policy_cfg.currency_symbol),
        "materials": materials,
        "materials_total": materials_total,
        "hardware": hardware,
        "hardware_total": hardware_total,
        "buyout": buyout,
        "buyout_included_total": buyout_included_total,
        "labor": labor,
        "labor_total": labor_total,
        "design_fee": design_fee,
        "delivery": delivery,
        "contingency": contingency,
        "discount": discount,
        "subtotal": subtotal,
        "gst": gst,
        "grand_total": grand_total,
        "payments": payments,
        "rooms": rooms,
    }

    html = template.render(**ctx)

    # Output path
    out_dir = project_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_path or (out_dir / "client-quote.html")
    if isinstance(out_path, Path):
        out_file = out_path
    else:
        out_file = Path(out_path)
    out_file.write_text(html, encoding="utf-8")
    return out_file

