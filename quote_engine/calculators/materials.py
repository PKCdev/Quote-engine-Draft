from __future__ import annotations

from typing import Any, Dict, List, Tuple
from ..normalize.materials import parse_material_name, price_from_attributes


def _parse_sheet_size(text: str) -> Tuple[float, float]:
    """Parse sizes like '1810 x 3620' into (mm_w, mm_h). Returns (0,0) if unknown."""
    if not text:
        return 0.0, 0.0
    t = text.lower().replace("mm", "").replace("×", "x")
    if "x" not in t:
        return 0.0, 0.0
    try:
        a, b = [s.strip() for s in t.split("x", 1)]
        return float(a or 0.0), float(b or 0.0)
    except Exception:
        return 0.0, 0.0


def _area_m2_from_sheet(s: Dict[str, Any], cat: Dict[str, Any]) -> float:
    # Prefer WOS-provided size
    if s.get("sheet_size"):
        w, h = _parse_sheet_size(str(s.get("sheet_size")))
        if w and h:
            return (w * h) / 1_000_000.0
    # Fallback to catalog sheet_size_mm
    sz = cat.get("sheet_size_mm")
    if isinstance(sz, (list, tuple)) and len(sz) == 2:
        try:
            w, h = float(sz[0]), float(sz[1])
            return (w * h) / 1_000_000.0
        except Exception:
            pass
    # Worst-case fallback to 1 m² to avoid zeroing costs
    return 1.0


def compute(
    sheets: List[Dict[str, Any]],
    materials_cat: Dict[str, Any],
    policy: Dict[str, Any],
    attr_pricing: Dict[str, Any] | None = None,
) -> float:
    """Compute sheet material cost using WOS sheet counts and material catalog.

    sheets: list of {material, qty, thickness?}
    materials_cat: YAML dict keyed by canonical material name with unit_cost_aud_ex_gst
    policy: may contain extra_sheet_waste (fraction)
    """
    extra_waste = float(policy.get("extra_sheet_waste", 0.0) or 0.0)
    total = 0.0
    for s in sheets:
        name = str(s.get("material", "")).strip()
        qty = int(s.get("qty", 0) or 0)
        cat = materials_cat.get(name, {})
        # Prefer explicit per-name price
        price_m2 = cat.get("price_per_m2_aud_ex_gst")
        if price_m2 is not None:
            price_m2 = float(price_m2 or 0.0)
            area_m2 = _area_m2_from_sheet(s, cat)
            total += qty * price_m2 * area_m2 * (1.0 + extra_waste)
        else:
            # Try attribute-based pricing if available
            price_attr = None
            if attr_pricing:
                attrs = parse_material_name(name)
                price_attr = price_from_attributes(attrs, attr_pricing)
            if price_attr is not None:
                area_m2 = _area_m2_from_sheet(s, cat)
                total += qty * float(price_attr) * area_m2 * (1.0 + extra_waste)
            else:
                # Fallback to unit per-sheet price if present, else 0
                unit = float(cat.get("unit_cost_aud_ex_gst", 0.0) or 0.0)
                total += qty * unit * (1.0 + extra_waste)
    return round(total, 2)


def breakdown(
    sheets: List[Dict[str, Any]],
    materials_cat: Dict[str, Any],
    policy: Dict[str, Any],
    attr_pricing: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    extra_waste = float(policy.get("extra_sheet_waste", 0.0) or 0.0)
    items: List[Dict[str, Any]] = []
    total = 0.0
    for s in sheets:
        name = str(s.get("material", "")).strip()
        qty = int(s.get("qty", 0) or 0)
        cat = materials_cat.get(name, {})
        area_per_sheet = _area_m2_from_sheet(s, cat)
        price_m2 = cat.get("price_per_m2_aud_ex_gst")
        chosen_source = "name"
        if price_m2 is None and attr_pricing:
            attrs = parse_material_name(name)
            price_attr = price_from_attributes(attrs, attr_pricing)
            if price_attr is not None:
                price_m2 = float(price_attr)
                chosen_source = "attributes"
        if price_m2 is None:
            # derive from unit sheet price if available, else 0
            unit = float(cat.get("unit_cost_aud_ex_gst", 0.0) or 0.0)
            if area_per_sheet > 0 and unit > 0:
                price_m2 = unit / area_per_sheet
                chosen_source = "unit"
            else:
                price_m2 = 0.0
                chosen_source = "unknown"
        total_area = qty * area_per_sheet
        cost = total_area * float(price_m2) * (1.0 + extra_waste)
        total += cost
        items.append(
            {
                "material": name,
                "qty": qty,
                "sheet_size": s.get("sheet_size") or (cat.get("sheet_size_mm") and f"{cat['sheet_size_mm'][0]} x {cat['sheet_size_mm'][1]}") or "",
                "area_m2": round(total_area, 3),
                "price_per_m2": round(float(price_m2), 2),
                "cost": round(cost, 2),
                "source": chosen_source,
            }
        )
    return {"items": items, "total": round(total, 2)}
