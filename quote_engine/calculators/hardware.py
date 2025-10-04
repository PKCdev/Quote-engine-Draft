from __future__ import annotations

from typing import Any, Dict, List


def compute(hardware: List[Dict[str, Any]], hardware_cat: Dict[str, Any], aliases: Dict[str, str] | None = None) -> float:
    total = 0.0
    aliases = aliases or {}
    for h in hardware:
        desc = str(h.get("description", "")).strip()
        # Alias mapping (user-confirmed mapping from WOS desc -> catalog key)
        key = aliases.get(desc, desc)
        qty = int(h.get("qty", 0) or 0)
        cat = hardware_cat.get(key, {})
        unit = float(cat.get("unit_price_aud_ex_gst", 0.0) or 0.0)
        pack = int(cat.get("pack_size", 1) or 1)
        if pack <= 0:
            pack = 1
        # Round up to pack multiples
        packs_needed = (qty + pack - 1) // pack
        total += packs_needed * unit * pack
    return round(total, 2)
