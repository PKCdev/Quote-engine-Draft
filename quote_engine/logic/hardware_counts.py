from __future__ import annotations

from typing import Dict, List


def classify_hardware(hardware_list: List[Dict]) -> Dict[str, int]:
    """Classify WOS hardware totals into coarse buckets.

    Returns counts for: drawer_kits, inner_drawers, hinges, adj_feet, bins, liftup.
    """
    totals = {
        "drawer_kits": 0,
        "inner_drawers": 0,
        "hinges": 0,
        "adj_feet": 0,
        "bins": 0,
        "liftup": 0,
    }
    for h in hardware_list or []:
        desc = (h.get("description") or "").lower()
        qty = int(h.get("qty", 0) or 0)
        # Feet / legs
        if "adj leg" in desc or "adj feet" in desc or "adjustable leg" in desc or "adjustable feet" in desc:
            totals["adj_feet"] += qty
            continue
        # Ignore supports as drawers
        if "drawer support" in desc:
            continue
        # Drawer kits
        if "drawer kit" in desc or ("inner drawer" in desc and "kit" in desc):
            # Try to recognize inner drawers separately
            if "inner" in desc:
                totals["inner_drawers"] += qty
            else:
                totals["drawer_kits"] += qty
            continue
        # Hinges
        if "hinge" in desc:
            totals["hinges"] += qty
            continue
        # Bins
        if "bin" in desc:
            totals["bins"] += qty
            continue
        # Lift-up mechanisms (Aventos, etc.)
        if "aventos" in desc or "liftup" in desc or "lift-up" in desc or "lift mechanism" in desc:
            totals["liftup"] += qty
            continue
    return totals

