from __future__ import annotations

from typing import Any, Dict, Optional
import re
from ..logic.hardware_counts import classify_hardware


def _factor_for_type(ptype: str, rules: Dict[str, Any]) -> float:
    return float(
        rules.get("types", {})
        .get(ptype, {})
        .get("area_factor_h_per_m2", rules.get("defaults", {}).get("area_factor_h_per_m2", 1.0))
    )


def _adders(rules: Dict[str, Any]) -> Dict[str, float]:
    d = rules.get("adders", {})
    return {
        "drawer": float(d.get("drawer_h", 0.3)),
        "door": float(d.get("door_h", 0.2)),
        "adj_shelf": float(d.get("adj_shelf_h", 0.1)),
        "fixed_shelf": float(d.get("fixed_shelf_h", 0.2)),
    }


def _complexity_multiplier(desc: str, rules: Dict[str, Any]) -> float:
    comp = 1.0
    cmap = rules.get("complexity", {})
    dlow = (desc or "").lower()
    for key, mult in cmap.items():
        if key.lower() in dlow:
            try:
                comp *= float(mult)
            except Exception:
                pass
    return comp


def estimate(
    products_data: Dict[str, Any],
    rules: Dict[str, Any],
    rates: Dict[str, Any],
    product_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Estimate assembly hours by product using area-weighted model.

    This MVP does not detect drawers/doors counts from MV (requires another input),
    so it applies base area model only. Adders can be integrated when counts are available.
    """
    shop_rate = float(rates.get("labor_rates", {}).get("shop", 150))
    total_hours = 0.0
    per_product = []
    min_hours = float(rates.get("assembly", {}).get("min_hours_per_product", 0.25))
    pov = product_overrides or {}

    # Config: minutes model
    base_min_per_m2 = float(rates.get("assembly", {}).get("base_minutes_per_m2", 44.44))
    min_min_per_prod = float(rates.get("assembly", {}).get("min_minutes_per_product", 10))
    setout_min_per_prod = float(rates.get("assembly", {}).get("setout_minutes_per_product", 5))
    madd = rules.get("minutes_adders", {})
    add_drawer = float(madd.get("drawer", 20))
    add_inner = float(madd.get("inner_drawer", 20))
    add_hinge = float(madd.get("hinge", 1))
    add_foot = float(madd.get("foot", 1))
    add_bin = float(madd.get("bin", 25))
    hinges_per_door = int(madd.get("hinges_per_door", 2))

    # Heuristic counts per product
    def infer_counts(desc: str, depth_mm: float) -> Dict[str, float]:
        d = desc.lower()
        counts = {"drawers": 0.0, "inner_drawers": 0.0, "doors": 0.0, "feet": 0.0, "bins": 0.0}
        # drawers
        m = re.search(r"(\d+)\s*drawer", d)
        if m:
            counts["drawers"] = float(m.group(1))
        if "inner drawer" in d:
            m2 = re.search(r"(\d+)\s*inner drawer", d)
            counts["inner_drawers"] = float(m2.group(1) if m2 else 1.0)
        # doors
        if "door" in d:
            m3 = re.search(r"(\d+)\s*door", d)
            counts["doors"] = float(m3.group(1) if m3 else 1.0)
        # bins
        if "bin" in d:
            counts["bins"] = 1.0
        # feet by depth heuristic
        counts["feet"] = 6.0 if (depth_mm and depth_mm >= 500) else 0.0
        return counts

    # Hardware totals for scaling
    hw_totals = classify_hardware((products_data.get("hardware", []) or [])) if isinstance(products_data, dict) else {}
    # But hardware list lives in WOS; attempt to pull from that via attached payload in products_data
    if not hw_totals and isinstance(products_data, dict):
        # No attached hardware; skip scaling
        hw_totals = {}

    predicted_totals = {"drawers": 0.0, "inner_drawers": 0.0, "hinges": 0.0, "feet": 0.0, "bins": 0.0}
    perprod_counts: Dict[str, Dict[str, float]] = {}
    for p in products_data.get("products", []):
        item_id = p.get("item")
        desc = p.get("description", "")
        depth = float(p.get("depth_mm", 0.0) or 0.0)
        c = infer_counts(desc, depth)
        # Hinges derived from doors
        c["hinges"] = c.get("doors", 0.0) * hinges_per_door
        perprod_counts[item_id] = c
        for k in predicted_totals:
            predicted_totals[k] += c.get(k, 0.0)

    # Scaling to hardware source of truth when present
    scales = {k: 1.0 for k in predicted_totals}
    if hw_totals:
        map_keys = {
            "drawers": "drawer_kits",
            "inner_drawers": "inner_drawers",
            "hinges": "hinges",
            "feet": "adj_feet",
            "bins": "bins",
        }
        for k, hwk in map_keys.items():
            actual = float(hw_totals.get(hwk, 0) or 0)
            predicted = float(predicted_totals.get(k, 0) or 0)
            if actual > 0 and predicted > 0:
                scales[k] = actual / predicted

    for p in products_data.get("products", []):
        ptype = p.get("description", "")
        area_m2 = float(p.get("area_m2", 0.0))
        qty = max(1, int(p.get("qty", 1)))
        comp = _complexity_multiplier(ptype, rules)
        item_id = p.get("item")
        # per-product overrides
        ov = pov.get(item_id, {}) if item_id else {}
        # exclude (buyout): remove from assembly time
        if ov.get("exclude"):
            hours = 0.0
        else:
            extra_comp = float(ov.get("complexity", 1.0) or 1.0)
            desc_l = (ptype or "").lower()
            # Special rule: Adjustable Kick fixed 20 minutes
            if "adjustable kick" in desc_l:
                minutes = 20.0 * comp * extra_comp
            else:
                # Minutes model
                area_min = max(min_min_per_prod, area_m2 * base_min_per_m2)
                c = perprod_counts.get(item_id, {})
                add_min = (
                    c.get("drawers", 0.0) * scales["drawers"] * add_drawer
                    + c.get("inner_drawers", 0.0) * scales["inner_drawers"] * add_inner
                    + c.get("hinges", 0.0) * scales["hinges"] * add_hinge
                    + c.get("feet", 0.0) * scales["feet"] * add_foot
                    + c.get("bins", 0.0) * scales["bins"] * add_bin
                )
                minutes = (area_min + add_min + setout_min_per_prod) * comp * extra_comp
            hours = (minutes / 60.0) * qty
        per_product.append({
            "item": p.get("item"),
            "description": ptype,
            "room": p.get("room"),
            "width_mm": p.get("width_mm"),
            "height_mm": p.get("height_mm"),
            "depth_mm": p.get("depth_mm"),
            "qty": qty,
            "hours": round(hours, 2),
        })
        total_hours += hours

    return {"hours": round(total_hours, 2), "cost": round(total_hours * shop_rate, 2), "products": per_product}
