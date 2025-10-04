from __future__ import annotations

from typing import Any, Dict, Optional
import re
from ..logic.hardware_counts import classify_hardware


def _norm(s: str) -> str:
    return s or ""


def _factor_for_type(ptype: str, rules: Dict[str, Any]) -> float:
    # install rules may be inside assembly rules under key 'install'
    install = rules.get("install", {})
    types = install.get("types", {})
    default = float(install.get("defaults", {}).get("area_factor_h_per_m2", 0.8))
    return float(types.get(ptype, {}).get("area_factor_h_per_m2", default))


def _complexity_multiplier(desc: str, rules: Dict[str, Any]) -> float:
    install = rules.get("install", {})
    cmap = install.get("complexity", {})
    dlow = (desc or "").lower()
    comp = 1.0
    for key, mult in cmap.items():
        if key.lower() in dlow:
            try:
                comp *= float(mult)
            except Exception:
                pass
    return comp


def estimate(
    products_data: Dict[str, Any],
    rates: Dict[str, Any],
    rules: Dict[str, Any],
    product_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Auto-estimate installation as area-weighted per product with complexity.

    If an explicit override exists in rates.defaults.install_hours (> 0), it is used instead.
    """
    override = float(rates.get("defaults", {}).get("install_hours", 0.0) or 0.0)
    # Crew allocation fractions and rates
    two_person_frac = float(rates.get("install_team", {}).get("two_person_fraction", 0.8) or 0.0)
    one_person_frac = float(rates.get("install_team", {}).get("one_person_fraction", 0.2) or 0.0)
    # Normalize fractions to sum to 1 if they are out of bounds
    total_frac = max(two_person_frac + one_person_frac, 0.0)
    if total_frac <= 0:
        two_person_frac, one_person_frac = 1.0, 0.0
        total_frac = 1.0
    else:
        two_person_frac /= total_frac
        one_person_frac /= total_frac
    two_person_rate = float(rates.get("install_team", {}).get("two_person_rate", 190) or 190)
    one_person_rate = float(rates.get("install_team", {}).get("one_person_rate", rates.get("labor_rates", {}).get("installer_billed", 95)) or 95)
    base_min_per_m2 = float(rates.get("install", {}).get("base_minutes_per_m2", 30))
    min_min_per_prod = float(rates.get("install", {}).get("min_minutes_per_product", 5))
    madd = rules.get("install_minutes_adders", {})
    add_drawer = float(madd.get("drawer", 5))
    add_inner = float(madd.get("inner_drawer", 5))
    add_hinge = float(madd.get("hinge", 2))
    add_foot = float(madd.get("foot", 1))
    add_bin = float(madd.get("bin", 10))

    if override > 0:
        # Override is site hours. Cost is weighted by crew allocation and their rates.
        site_hours = override
        blended_rate = two_person_frac * two_person_rate + one_person_frac * one_person_rate
        return {
            "hours": round(site_hours, 2),
            "cost": round(site_hours * blended_rate, 2),
            "products": [],
            "person_hours": round(site_hours * (two_person_frac * 2.0 + one_person_frac * 1.0), 2),
            "two_person_fraction": round(two_person_frac, 4),
            "one_person_fraction": round(one_person_frac, 4),
            "two_person_rate": round(two_person_rate, 2),
            "one_person_rate": round(one_person_rate, 2),
            "blended_rate": round(blended_rate, 2),
        }

    # Auto-estimate from products
    total_person_hours = 0.0
    per_product = []

    # Heuristic counts per product + hardware scaling
    def infer_counts(desc: str, depth_mm: float) -> Dict[str, float]:
        d = (desc or "").lower()
        counts = {"drawers": 0.0, "inner_drawers": 0.0, "doors": 0.0, "feet": 0.0, "bins": 0.0}
        m = re.search(r"(\d+)\s*drawer", d)
        if m:
            counts["drawers"] = float(m.group(1))
        if "inner drawer" in d:
            m2 = re.search(r"(\d+)\s*inner drawer", d)
            counts["inner_drawers"] = float(m2.group(1) if m2 else 1.0)
        if "door" in d:
            m3 = re.search(r"(\d+)\s*door", d)
            counts["doors"] = float(m3.group(1) if m3 else 1.0)
        if "bin" in d:
            counts["bins"] = 1.0
        counts["feet"] = 6.0 if (depth_mm and depth_mm >= 500) else 0.0
        return counts

    # Hardware totals from products_data attached? otherwise leave scales at 1
    hw_totals = classify_hardware((products_data.get("hardware", []) or [])) if isinstance(products_data, dict) else {}
    predicted_totals = {"drawers": 0.0, "inner_drawers": 0.0, "hinges": 0.0, "feet": 0.0, "bins": 0.0}
    perprod_counts: Dict[str, Dict[str, float]] = {}
    for p in products_data.get("products", []):
        desc = p.get("description", "")
        depth = float(p.get("depth_mm", 0.0) or 0.0)
        c = infer_counts(desc, depth)
        c["hinges"] = c.get("doors", 0.0) * float(rules.get("minutes_adders", {}).get("hinges_per_door", 2))
        perprod_counts[p.get("item")] = c
        for k in predicted_totals:
            predicted_totals[k] += c.get(k, 0.0)
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
    pov = product_overrides or {}
    for p in products_data.get("products", []):
        ptype = _norm(p.get("description", ""))
        area_m2 = float(p.get("area_m2", 0.0))
        qty = max(1, int(p.get("qty", 1)))
        factor = _factor_for_type(ptype, rules)
        comp = _complexity_multiplier(ptype, rules)
        item_id = p.get("item")
        ov = pov.get(item_id, {}) if item_id else {}
        if ov.get("exclude"):
            hours = 0.0
        else:
            desc_l = (p.get("description") or "").lower()
            width_mm = float(p.get("width_mm", 0.0) or 0.0)
            width_m = width_mm / 1000.0
            if "floating shelf" in desc_l:
                minutes = 30.0 * width_m * comp
            elif "adjustable kick" in desc_l:
                minutes = 15.0 * width_m * comp
            else:
                area_min = max(min_min_per_prod, area_m2 * base_min_per_m2)
                c = perprod_counts.get(item_id, {})
                add_min = (
                    c.get("drawers", 0.0) * scales["drawers"] * add_drawer
                    + c.get("inner_drawers", 0.0) * scales["inner_drawers"] * add_inner
                    + c.get("hinges", 0.0) * scales["hinges"] * add_hinge
                    + c.get("feet", 0.0) * scales["feet"] * add_foot
                    + c.get("bins", 0.0) * scales["bins"] * add_bin
                )
                minutes = (area_min + add_min) * comp
            hours = (minutes / 60.0) * qty
        per_product.append({
            "item": p.get("item"),
            "description": p.get("description"),
            "room": p.get("room"),
            "width_mm": p.get("width_mm"),
            "height_mm": p.get("height_mm"),
            "depth_mm": p.get("depth_mm"),
            "qty": qty,
            "hours": round(hours, 2),
        })
        total_person_hours += hours

    # Convert person-hours to site-hours using crew allocation
    denom = two_person_frac * 2.0 + one_person_frac * 1.0
    site_hours = total_person_hours / max(denom, 0.0001)
    blended_rate = two_person_frac * two_person_rate + one_person_frac * one_person_rate
    cost = site_hours * blended_rate
    return {
        "hours": round(site_hours, 2),
        "cost": round(cost, 2),
        "products": per_product,
        "person_hours": round(total_person_hours, 2),
        "two_person_fraction": round(two_person_frac, 4),
        "one_person_fraction": round(one_person_frac, 4),
        "two_person_rate": round(two_person_rate, 2),
        "one_person_rate": round(one_person_rate, 2),
        "blended_rate": round(blended_rate, 2),
    }
