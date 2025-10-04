from __future__ import annotations

from typing import Any, Dict


def allocate(drafting_hours: float, pm_hours: float, assembly_hours: float, rates: Dict[str, Any]) -> float:
    monthly = float(rates.get("overhead", {}).get("monthly_aud", 6516))
    internal_hours = float(rates.get("overhead", {}).get("internal_hours", 140))
    oh_per_hour = monthly / max(internal_hours, 1)
    total_internal = float(drafting_hours) + float(pm_hours) + float(assembly_hours)
    return round(oh_per_hour * total_internal, 2)

