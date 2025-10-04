from __future__ import annotations

from typing import Any, Dict


def price(totals: Dict[str, float], rates: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, float]:
    ex_gst_subtotal = sum(float(v or 0.0) for v in totals.values())
    gm_target = float(policy.get("target_margin", 0.30))
    gst_rate = float(rates.get("taxes", {}).get("gst", 0.10))

    price_ex_gst = ex_gst_subtotal / max(1.0 - gm_target, 0.0001)
    gst = price_ex_gst * gst_rate
    total_inc_gst = price_ex_gst + gst

    # Rounding to nearest $10 by default
    rounding = int(policy.get("rounding", 10))
    if rounding > 0:
        import math

        price_ex_gst = math.floor((price_ex_gst + rounding / 2) / rounding) * rounding
        gst = price_ex_gst * gst_rate
        total_inc_gst = price_ex_gst + gst

    return {
        "subtotal_ex_gst": round(ex_gst_subtotal, 2),
        "price_ex_gst": round(price_ex_gst, 2),
        "gst": round(gst, 2),
        "total_inc_gst": round(total_inc_gst, 2),
    }

