from __future__ import annotations

import re
from typing import Dict, Optional


FINISH_KEYWORDS = [
    "woodmatt",
    "ravine",
    "matt",
    "matte",
    "gloss",
    "texture",
    "sheen",
    "silk",
]

SUBSTRATES = ["HPL", "MDF", "PBD", "PB", "CL"]
SUPPLIERS = ["LX", "PT", "GBI"]

TYPICAL_THICKNESSES = [0.7, 12, 16, 18, 25, 32, 33]


def _closest_thickness(value: float) -> float:
    if value <= 0:
        return 0.0
    return min(TYPICAL_THICKNESSES, key=lambda t: abs(t - value))


def parse_material_name(name: str) -> Dict[str, Optional[str]]:
    """Parse a material string like 'G PT Black Woodmatt 18mm MDF - 36x18'.

    Returns dict with supplier, finish, thickness_mm (float), substrate.
    Color is ignored for pricing.
    """
    if not name:
        return {"supplier": None, "finish": None, "thickness_mm": None, "substrate": None}
    s = name.replace("-", " ")
    tokens = re.split(r"\s+", s)

    # Supplier
    supplier = None
    for tok in tokens:
        t = tok.upper()
        if t in SUPPLIERS:
            supplier = t
            break

    # Finish
    lower = s.lower()
    finish = None
    for f in FINISH_KEYWORDS:
        if f in lower:
            finish = f
            break

    # Thickness (first number followed by mm, avoid sheet sizes without 'mm')
    thickness = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm", lower)
    if m:
        try:
            thickness = float(m.group(1))
            thickness = _closest_thickness(thickness)
        except Exception:
            thickness = None

    # Substrate
    substrate = None
    for sub in SUBSTRATES:
        if re.search(rf"\b{sub}\b", name, re.IGNORECASE):
            # Normalize PB -> PBD if present as PB only
            substrate = "PBD" if sub.upper() == "PB" else sub.upper()
            break
    # Common heuristic: if 'MDF' appears anywhere, assume MDF
    if substrate is None and re.search(r"\bMDF\b", name, re.IGNORECASE):
        substrate = "MDF"

    return {
        "supplier": supplier,
        "finish": finish,
        "thickness_mm": thickness,
        "substrate": substrate,
    }


def price_from_attributes(attrs: Dict[str, Optional[str]], pricing: Dict) -> Optional[float]:
    """Lookup price/m² in a pricing catalog based on supplier+finish+thickness+substrate.

    Catalog structure (configs/materials_pricing.yaml):
    entries:
      - supplier: PT
        finish: woodmatt
        substrate: MDF
        thickness_mm: 18
        price_per_m2_aud_ex_gst: 68.72
    """
    if not pricing:
        return None
    entries = pricing.get("entries", [])
    s = (attrs.get("supplier") or "").upper()
    f = (attrs.get("finish") or "").lower()
    u = (attrs.get("substrate") or "").upper()
    t = attrs.get("thickness_mm")
    # exact match first
    for e in entries:
        if (
            (e.get("supplier", "").upper() == s)
            and (e.get("finish", "").lower() == f)
            and (str(e.get("substrate", "")).upper() == u)
            and (float(e.get("thickness_mm", 0)) == float(t or 0))
        ):
            return float(e.get("price_per_m2_aud_ex_gst", 0))
    # relaxed match (ignore substrate if missing)
    for e in entries:
        if (
            (e.get("supplier", "").upper() == s)
            and (e.get("finish", "").lower() == f)
            and (float(e.get("thickness_mm", 0)) == float(t or 0))
        ):
            return float(e.get("price_per_m2_aud_ex_gst", 0))
    return None

