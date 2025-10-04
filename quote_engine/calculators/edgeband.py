from __future__ import annotations

from typing import Any, Dict, List, Optional


def compute(
    edgeband: List[Dict[str, Any]],
    bands_cat: Dict[str, Any],
    policy: Dict[str, Any],
    rates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute edgeband cost and time from WOS LM and band catalog.

    Cost per spec: cost = LM * price_per_m + setup_cost per distinct spec.
    Time (minutes) if rates.edgeband set: minutes = minutes_per_m * total_lm + setup_minutes * distinct_specs.
    """
    total_cost = 0.0
    setups_cost = 0.0
    total_lm = 0.0
    seen = set()
    items: List[Dict[str, Any]] = []
    for b in edgeband:
        spec = str(b.get("spec", "")).strip()
        lm = float(b.get("lm", 0.0) or 0.0)
        total_lm += lm
        cat = bands_cat.get(spec, {})
        price_m = cat.get("price_per_m")
        # Phrase pricing fallback
        if (price_m is None) and rates and isinstance(rates.get("edgeband"), dict):
            sp = spec.lower()
            if "woodmatt" in sp:
                price_m = rates["edgeband"].get("phrase_pricing", {}).get("woodmatt")
            elif (" black" in (" " + sp)) and ("woodmatt" not in sp):
                price_m = rates["edgeband"].get("phrase_pricing", {}).get("plain_black")
        # Global fallback
        if (price_m is None) and rates and isinstance(rates.get("edgeband"), dict):
            price_m = rates["edgeband"].get("price_per_m")
        price_m = float(price_m or 0.0)
        cost = lm * price_m
        total_cost += cost
        if spec and spec not in seen:
            seen.add(spec)
            setups_cost += float(cat.get("setup_cost", 0.0) or 0.0)
        items.append({"spec": spec, "lm": round(lm, 2), "price_per_m": round(price_m, 2), "cost": round(cost, 2)})
    minutes = 0.0
    if rates and isinstance(rates.get("edgeband"), dict):
        minutes += float(rates["edgeband"].get("minutes_per_m", 0.0) or 0.0) * total_lm
        minutes += float(rates["edgeband"].get("setup_minutes", 0.0) or 0.0) * len(seen)
    return {"cost": round(total_cost + setups_cost, 2), "hours": round(minutes / 60.0, 2), "items": items}


def time_from_parts(parts_data: Dict[str, Any], rates: Dict[str, Any]) -> float:
    """Compute edgebanding time (hours) from part edge flags.

    Each non-empty EB flag (EB Width/Length 1/2) counts as one edged side; default 1 minute per edge.
    """
    minutes_per_edge = float(((rates or {}).get("edgeband", {}) or {}).get("minutes_per_edge", 1.0) or 1.0)
    count_edges = 0
    for p in (parts_data or {}).get("parts", []):
        try:
            qty = int(p.get("qty", 0) or 0)
        except Exception:
            qty = 0
        flags = (p.get("eb_flags") or {})
        for key in ("EBW1", "EBL1", "EBW2", "EBL2"):
            val = flags.get(key)
            if val is not None and str(val).strip() != "":
                count_edges += qty if qty > 0 else 1
    minutes = count_edges * minutes_per_edge
    return round(minutes / 60.0, 2)
