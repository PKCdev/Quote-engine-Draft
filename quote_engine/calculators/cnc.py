from __future__ import annotations

from typing import Any, Dict
from ..calculators.materials import _parse_sheet_size  # reuse parser if needed


def estimate_from_materials(mat_bd: Dict[str, Any], rates: Dict[str, Any], material_overrides: Dict[str, Any] | None = None) -> Dict[str, Dict[str, float]]:
    """Estimate CNC and Panel Saw time/cost from material breakdown and overrides.

    - CNC time = total CNC area (m²) / sqm_per_hour
    - Panel saw time = sheets_on_saw × minutes_per_sheet / 60
    Materials flagged with panel_saw=True are excluded from CNC area and counted for panel saw time.
    """
    sqm_per_hour = float(rates.get("cnc_area", {}).get("sqm_per_hour", 10) or 10)
    panel_min_per_sheet = float(rates.get("panel_saw", {}).get("minutes_per_sheet", 15) or 15)
    # Prefer rental cost rates for internal cost calculations
    cnc_rate = float((rates.get("machine_rental", {}) or {}).get("cnc", (rates.get("machine_rates", {}) or {}).get("cnc", rates.get("labor_rates", {}).get("shop", 150))))
    panel_rate = float((rates.get("machine_rental", {}) or {}).get("panel_saw", (rates.get("machine_rates", {}) or {}).get("panel_saw", 120)))

    cnc_area = 0.0
    saw_sheets = 0
    overrides = material_overrides or {}
    for it in mat_bd.get("items", []):
        mat = str(it.get("material") or "")
        qty = int(it.get("qty", 0) or 0)
        area_m2 = float(it.get("area_m2", 0.0) or 0.0)
        ov = overrides.get(mat, {}) if mat else {}
        if ov.get("panel_saw"):
            saw_sheets += qty
        else:
            cnc_area += area_m2

    cnc_hours = cnc_area / max(sqm_per_hour, 0.0001)
    panel_hours = (saw_sheets * panel_min_per_sheet) / 60.0
    return {
        "cnc": {"hours": round(cnc_hours, 2), "cost": round(cnc_hours * cnc_rate, 2)},
        "panel_saw": {"hours": round(panel_hours, 2), "cost": round(panel_hours * panel_rate, 2)},
    }
