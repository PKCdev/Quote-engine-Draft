from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _money(x: float, symbol: str = "$") -> str:
    try:
        return f"{symbol}{float(x):,.2f}"
    except Exception:
        return f"{symbol}0.00"

def _round_price(val: float, rounding: float) -> float:
    try:
        if rounding and rounding > 0:
            import math
            return float(math.floor((float(val) + rounding / 2.0) / rounding) * rounding)
    except Exception:
        pass
    return float(val)


def render_client_quote(
    project_name: str,
    materials: List[Dict[str, Any]],
    edgeband: List[Dict[str, Any]],
    hardware: List[Dict[str, Any]],
    assembly: Dict[str, Any],
    install: Dict[str, Any],
    price: Dict[str, Any],
    rates: Dict[str, Any],
    # Additional context for Cambridge-style output
    policy: Dict[str, Any] | None = None,
    category_totals: Dict[str, Any] | None = None,
    materials_breakdown: Dict[str, Any] | None = None,
    products: Dict[str, Any] | None = None,
    buyout: Dict[str, Any] | None = None,
    hardware_catalog: Dict[str, Any] | None = None,
    hardware_aliases: Dict[str, str] | None = None,
    meta: Dict[str, Any] | None = None,
) -> str:
    policy = policy or {}
    category_totals = category_totals or {}
    materials_breakdown = materials_breakdown or {"items": []}
    products = products or {"products": []}
    buyout = buyout or {"buyouts": []}
    hardware_catalog = hardware_catalog or {}
    hardware_aliases = hardware_aliases or {}
    meta = meta or {}

    currency_symbol = policy.get("currency_symbol", "$")
    gst_rate = rates.get("taxes", {}).get("gst", 0.10)
    gst_percent = round(float(gst_rate) * 100)
    waste_frac = float(policy.get("extra_sheet_waste", 0.0) or 0.0)
    waste_percent = int(round(waste_frac * 100))

    # Enrich hardware lines with unit price and total (if catalog has it)
    hw_lines: List[Dict[str, Any]] = []
    for h in hardware:
        desc = str(h.get("description", "")).strip()
        qty = int(h.get("qty", 0) or 0)
        key = hardware_aliases.get(desc, desc)
        cat = hardware_catalog.get(key, {})
        unit = float(cat.get("unit_price_aud_ex_gst", 0.0) or 0.0)
        pack = int(cat.get("pack_size", 1) or 1)
        packs_needed = (qty + pack - 1) // pack if pack > 0 else qty
        total = packs_needed * unit * pack if unit else 0.0
        hw_lines.append(
            {
                "name": desc,
                "qty": qty,
                "unit_cost": unit if unit else None,
                "total_cost": total if total else None,
            }
        )

    # Group products by room for display
    rooms: Dict[str, Dict[str, Any]] = {}
    for p in products.get("products", []) or []:
        room = (p.get("room") or "").strip() or "General"
        code = room.split(" ")[0] if room else ""
        key = room
        entry = rooms.setdefault(key, {"code": code, "name": room, "products": []})
        # Compose dimensions text if available
        dims = []
        w = p.get("width_mm"); hgt = p.get("height_mm"); d = p.get("depth_mm")
        if w or hgt or d:
            try:
                dims = [f"{int(w)}", f"{int(hgt)}", f"{int(d)}"]
            except Exception:
                dims = [str(w or 0), str(hgt or 0), str(d or 0)]
        dim_text = "×".join(dims) if dims else ""
        entry["products"].append({
            "index": f"#{p.get('item')}" if p.get("item") else "",
            "name": p.get("description") or "",
            "dimensions": dim_text,
        })
    rooms_list = [
        {"code": v.get("code", ""), "name": v.get("name", ""), "count": len(v.get("products", [])), "products": v.get("products", [])}
        for v in rooms.values()
    ]

    # Payment schedule amounts
    payments: List[Dict[str, Any]] = []
    for m in policy.get("payment_schedule", []) or []:
        base = (price.get("total_inc_gst") if (m.get("base") == "grand_total") else price.get("price_ex_gst")) or 0.0
        amt = (float(m.get("percent", 0.0)) / 100.0) * float(base)
        payments.append({"name": m.get("name"), "percent": m.get("percent"), "amount": amt, "description": m.get("description", "")})

    # Parties
    client = meta.get("client", {})
    company = meta.get("company", {})

    # Compute allowances and surcharges for display
    allowances_flat = float(policy.get("allowances", {}).get("flat_aud_ex_gst", 0.0) or 0.0)
    sc = policy.get("surcharges", {}) or {}
    surcharge_pct = float(sc.get("warranty_percent", 0.0) or 0.0) + float(sc.get("contingency_percent", 0.0) or 0.0) + float(sc.get("merchant_percent", 0.0) or 0.0)
    # Base subtotal for surcharges = sum of category totals + allowances (ex-GST)
    subtotal_for_surcharges = float(
        (category_totals.get("materials", 0) or 0)
        + (category_totals.get("edgeband", 0) or 0)
        + (category_totals.get("hardware", 0) or 0)
        + (category_totals.get("cnc", 0) or 0)
        + (category_totals.get("panel_saw", 0) or 0)
        + (category_totals.get("assembly", 0) or 0)
        + (category_totals.get("install", 0) or 0)
        + (category_totals.get("overhead", 0) or 0)
        + allowances_flat
    )
    surcharge_total = subtotal_for_surcharges * (surcharge_pct / 100.0) if surcharge_pct else 0.0

    # Compute distributed margin factor and display amounts per category
    m_target = float(policy.get("target_margin", 0.30) or 0.30)
    rounding_step = float(policy.get("rounding", 10) or 10)
    factor = 1.0 / max(1.0 - m_target, 0.0001)

    # Prefer totals passed in for allowances/surcharges if present
    if category_totals.get("allowances") is not None:
        allowances_flat = float(category_totals.get("allowances") or 0.0)
    if category_totals.get("surcharges") is not None:
        surcharge_total = float(category_totals.get("surcharges") or 0.0)

    # Build category display map with margin distributed (client-facing)
    cat_order = [
        "materials",
        "edgeband",
        "hardware",
        "cnc",
        "panel_saw",
        "assembly",
        "install",
        "overhead",
    ]
    category_display: Dict[str, float] = {}
    for key in cat_order:
        val = float(category_totals.get(key, 0.0) or 0.0)
        category_display[key] = round(val * factor, 2)
    allowances_display = round(float(allowances_flat or 0.0) * factor, 2)
    surcharge_display = round(float(surcharge_total or 0.0) * factor, 2)

    subtotal_display = round(sum(category_display.values()) + allowances_display, 2)
    price_sum_no_round = subtotal_display + surcharge_display
    # Align categories with final rounded price by assigning rounding delta to 'install'
    price_final = float(price.get("price_ex_gst", 0.0) or 0.0)
    delta = round(price_final - price_sum_no_round, 2)
    category_display["install"] = round(category_display.get("install", 0.0) + delta, 2)
    # Recompute subtotal display after delta
    subtotal_display = round(sum(category_display.values()) + allowances_display, 2)

    tmpl_dir = Path(__file__).resolve().parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(tmpl_dir)), autoescape=select_autoescape(["html", "xml"]))
    template = env.get_template("client_quote.html.j2")
    return template.render(
        project_name=project_name,
        client=client,
        company=company,
        materials_breakdown=materials_breakdown,
        hardware=hw_lines,
        buyouts=buyout.get("buyouts", []),
        assembly=assembly,
        install=install,
        category_totals=category_totals,
        category_display=category_display,
        price=price,
        policy=policy,
        gst_percent=gst_percent,
        waste_percent=waste_percent,
        currency_symbol=currency_symbol,
        rooms=rooms_list,
        money=lambda x: _money(x, currency_symbol),
        allowances_display=allowances_display,
        subtotal_display=subtotal_display,
        surcharge_pct=surcharge_pct,
        surcharge_display=surcharge_display,
        rounding_step=int(round(rounding_step)),
    )
